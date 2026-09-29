import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { OrgSwitcher } from "./org-switcher";
import { useSession } from "@/lib/store/session";
import type { ApiOrg, ApiUser } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("@/lib/api/orgs", () => ({ createOrg: vi.fn(), listOrgs: vi.fn() }));
vi.mock("@/lib/api/tokens", () => ({ setActiveOrgId: vi.fn() }));

function org(id: string, name: string): ApiOrg {
  return { id, name, slug: name.toLowerCase(), plan: "free", avatar_url: null } as ApiOrg;
}

function user(isStaff: boolean): ApiUser {
  return { id: "u1", email: "a@b.c", full_name: "A", is_staff: isStaff } as ApiUser;
}

/** Seed the real zustand store, render, and open the dropdown.
 *
 * Radix opens on pointerdown, which jsdom's `click` doesn't imply — the keyboard path is
 * both reliable here and the one a keyboard user takes. Resolves once the menu is really
 * open, so the "is absent" assertions below can't pass against a closed menu.
 */
async function renderSwitcher({ isStaff, orgs }: { isStaff: boolean; orgs: ApiOrg[] }) {
  useSession.setState({ user: user(isStaff), orgs, activeOrgId: orgs[0]?.id ?? null, ready: true });
  const result = render(<OrgSwitcher collapsed={false} />);
  const trigger = screen.getByRole("button");
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "Enter" });
  await screen.findByRole("menu");
  return result;
}

beforeEach(() => {
  vi.clearAllMocks();
  useSession.setState({ user: null, orgs: [], activeOrgId: null, ready: false });
});

describe("OrgSwitcher", () => {
  it("hides 'New organization' from a client", async () => {
    // Each client gets one org, provisioned for them — creating another isn't theirs to do.
    await renderSwitcher({ isStaff: false, orgs: [org("o1", "Acme")] });

    // The menu really is open (their own org is listed) — so the absence below is real.
    expect(screen.getByRole("menuitem", { name: /Acme/ })).toBeInTheDocument();
    expect(screen.queryByText("New organization")).toBeNull();
  });

  it("still shows the switcher itself for a single-org client", () => {
    // Closed state: the whole switcher stays for a client with one org, rather than
    // disappearing. Checked without opening — Radix hides outside content from the
    // accessibility tree while the menu is open.
    useSession.setState({
      user: user(false),
      orgs: [org("o1", "Acme")],
      activeOrgId: "o1",
      ready: true,
    });
    render(<OrgSwitcher collapsed={false} />);

    const trigger = screen.getByRole("button");
    expect(within(trigger).getByText("Acme")).toBeInTheDocument();
  });

  it("names the org without claiming it is on a plan", () => {
    // The trigger used to render "Free plan" under the name. Vicero has no tiers — every
    // org brings its own provider keys — so that advertised a distinction that does not
    // exist and an upgrade that cannot be bought.
    useSession.setState({
      user: user(false),
      orgs: [org("o1", "Acme")],
      activeOrgId: "o1",
      ready: true,
    });
    render(<OrgSwitcher collapsed={false} />);

    const trigger = screen.getByRole("button");
    expect(within(trigger).getByText("Acme")).toBeInTheDocument();
    expect(within(trigger).queryByText(/plan/i)).toBeNull();
  });

  it("lets a client switch between orgs they were invited to", async () => {
    // Multi-org membership via invite must keep working — an agency might invite the same
    // person into more than one client org.
    await renderSwitcher({ isStaff: false, orgs: [org("o1", "Acme"), org("o2", "Globex")] });

    expect(await screen.findByRole("menuitem", { name: /Acme/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Globex/ })).toBeInTheDocument();
    expect(screen.queryByText("New organization")).toBeNull();
  });

  it("shows 'New organization' to staff", async () => {
    await renderSwitcher({ isStaff: true, orgs: [org("o1", "Acme")] });
    expect(await screen.findByText("New organization")).toBeInTheDocument();
  });

  it("opens the create dialog for staff", async () => {
    await renderSwitcher({ isStaff: true, orgs: [org("o1", "Acme")] });

    fireEvent.click(await screen.findByText("New organization"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Organization name")).toBeInTheDocument();
  });
});
