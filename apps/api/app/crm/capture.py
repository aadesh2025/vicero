"""Turn contact details shared in chat into a CRM person, and recognise returning ones."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.crm.extract import ExtractedContact, extract_contact, find_contact_hints
from app.models import Contact, Conversation, CrmContact, Organization

log = get_logger("crm.capture")


async def _find_person(
    session: AsyncSession, org_id: uuid.UUID, found: ExtractedContact
) -> CrmContact | None:
    """Match on email OR phone — never on name.

    Names collide, vary in spelling, and aren't unique; matching on one would silently
    merge two customers' histories, which is far worse than leaving a duplicate record.
    """
    conditions = []
    if found.email:
        conditions.append(CrmContact.email == found.email)
    if found.phone:
        conditions.append(CrmContact.phone == found.phone)
    if not conditions:
        return None
    stmt = (
        select(CrmContact)
        .where(CrmContact.organization_id == org_id, or_(*conditions))
        .order_by(CrmContact.created_at.asc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _learn(person: CrmContact, found: ExtractedContact) -> list[str]:
    """Fill blanks only. A detail already on file isn't overwritten by a later guess."""
    learned: list[str] = []
    for field in ("display_name", "email", "phone"):
        value = found.name if field == "display_name" else getattr(found, field)
        if value and not getattr(person, field):
            setattr(person, field, value)
            learned.append(field)
    return learned


async def capture_from_message(
    session: AsyncSession, conversation: Conversation, text: str
) -> CrmContact | None:
    """Best-effort capture from one inbound message. Returns the person, if any.

    Runs on every customer message, so the cheap gate comes first: no email- or phone-shaped
    text means no LLM call at all, which is the overwhelmingly common case.
    """
    org = await session.get(Organization, conversation.organization_id)
    if org is None or not org.auto_crm_capture_enabled:
        return None

    hints = find_contact_hints(text)
    if not hints.any():
        return None  # the gate: nothing contact-shaped, so nothing to spend a model on

    found = await extract_contact(session, conversation.organization_id, text, hints)
    if not (found.email or found.phone):
        return None  # a name alone can't identify anyone

    contact = (
        await session.get(Contact, conversation.contact_id) if conversation.contact_id else None
    )

    person = await _find_person(session, conversation.organization_id, found)
    created = person is None
    changed = created
    if person is None:
        person = CrmContact(
            organization_id=conversation.organization_id,
            display_name=found.name or (contact.display_name if contact else None),
            email=found.email,
            phone=found.phone,
        )
        session.add(person)
        await session.flush()
        log.info("crm_person_created", person=str(person.id), conversation=str(conversation.id))
    else:
        learned = _learn(person, found)
        if learned:
            changed = True
            log.info("crm_person_enriched", person=str(person.id), fields=",".join(learned))

    # Link this channel's handle to the person — including a channel they've never used
    # before, which is what makes a returning customer recognisable anywhere.
    if contact is not None and contact.crm_contact_id != person.id:
        contact.crm_contact_id = person.id
        log.info(
            "crm_identity_linked",
            person=str(person.id),
            channel=contact.channel,
            contact=str(contact.id),
        )
    await session.flush()
    if changed:
        from app.webhooks.events import emit_lead_captured

        await emit_lead_captured(session, conversation, person, created=created)
    return person
