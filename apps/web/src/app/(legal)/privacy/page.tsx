import type { Metadata } from "next";
import Link from "next/link";
import { LegalPage, LegalSection } from "@/components/legal/legal-page";
import { LEGAL_EMAIL, LEGAL_LOCATION, LEGAL_OPERATOR } from "@/lib/legal";

export const metadata: Metadata = {
  title: "Privacy Policy — Vicero",
  description:
    "What personal data Vicero collects, why, how AI providers and Meta fit in, how long we keep it, and how to have it deleted.",
};

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy Policy"
      intro={`Vicero is an AI chatbot and automation platform operated by ${LEGAL_OPERATOR}, ${LEGAL_LOCATION} ("we", "us"). This policy explains what personal data we handle when you use Vicero or talk to a chatbot that runs on it.`}
    >
      <LegalSection title="1. Our role">
        <p>
          <strong>Our customers</strong> (businesses and individuals who build chatbots on Vicero) decide what their chatbot
          does and who it talks to. For the conversations their end users have with those chatbots, the customer is the
          data controller and Vicero is the data processor acting on the customer&apos;s instructions.
        </p>
        <p>
          For our own customer accounts (sign-up, billing, security, support) we are the controller. If you are an end user
          of a chatbot and have a question about your conversation, the business that runs the chatbot is your first point of
          contact; you can also write to us (section 11) and we will help route or act on the request.
        </p>
      </LegalSection>

      <LegalSection title="2. What we collect">
        <ul>
          <li>
            <strong>Account data:</strong> name, email address and sign-in credentials (passwords are stored only as a salted
            hash).
          </li>
          <li>
            <strong>Chatbot configuration:</strong> agent names, instructions, models, settings and automations a customer
            sets up.
          </li>
          <li>
            <strong>Knowledge-base documents:</strong> files, text and web pages customers upload for their chatbot to draw
            on, and the searchable index we derive from them.
          </li>
          <li>
            <strong>End-user conversations:</strong> the messages people send to a chatbot and the replies it gives, with
            timestamps and the channel used.
          </li>
          <li>
            <strong>Connected channels (WhatsApp Business, Facebook Messenger, Instagram direct messages):</strong> the
            page or account IDs that are connected, sender IDs, names and profile pictures where the platform provides them,
            message content, and the access tokens that let us send and receive on the customer&apos;s behalf. Access tokens
            are stored encrypted.
          </li>
          <li>
            <strong>Technical and usage data:</strong> IP address, device and browser type, log entries, usage counts and
            audit records, used to run and protect the service.
          </li>
        </ul>
      </LegalSection>

      <LegalSection title="3. Why we use it">
        <ul>
          <li>To run the chatbot: receive a message, produce a reply and send it back on the right channel.</li>
          <li>To store and show conversations to the customer&apos;s team, including hand-over to a human.</li>
          <li>To provide analytics and usage reporting to the customer.</li>
          <li>To bill customers and administer plans.</li>
          <li>To keep the service secure, prevent abuse, debug problems and meet legal obligations.</li>
        </ul>
      </LegalSection>

      <LegalSection title="4. AI processing">
        <p>
          To generate a reply, the text of a conversation, together with relevant passages from the customer&apos;s
          knowledge base and the chatbot&apos;s instructions, is sent to an AI model provider (for example Groq, OpenAI,
          Anthropic or Google, depending on how the customer configured the chatbot). Customers may also supply their own
          provider key, in which case the request is made under that account. We do not use customer or end-user content to
          train our own models, and we send providers only what is needed to produce the reply.
        </p>
        <p>
          Replies are generated automatically and can be wrong. They should not be treated as professional advice.
        </p>
      </LegalSection>

      <LegalSection title="5. Who we share data with">
        <p>We do not sell personal data. We share it only with:</p>
        <ul>
          <li>
            <strong>Infrastructure and AI subprocessors</strong> that host the service or process requests for us (cloud
            hosting, databases, email delivery, AI model providers), bound to use it only to provide their service to us.
          </li>
          <li>
            <strong>Meta Platforms</strong> (WhatsApp, Messenger, Instagram) as needed to send and receive messages on
            channels a customer has connected. Meta handles that data under its own terms and privacy policy.
          </li>
          <li>
            <strong>Authorities or advisers</strong> where the law requires it or to protect our rights, and a successor if
            the business is transferred (with notice to you).
          </li>
        </ul>
      </LegalSection>

      <LegalSection title="6. Retention and deletion">
        <p>
          Customer account data and conversations are kept while the account is active, or for as long as the customer
          chooses to keep them. Customers can delete conversations, contacts, knowledge-base documents, channel connections
          or the whole organization from the dashboard. When an organization is deleted, or when we receive a verified
          deletion request, we delete the associated data within 30 days. Some records (for example invoices and security
          logs) may be kept longer where the law or a legitimate security need requires it, and encrypted backups are
          overwritten on their normal cycle.
        </p>
        <p>
          See <Link href="/data-deletion">how to request deletion</Link>.
        </p>
      </LegalSection>

      <LegalSection title="7. Security">
        <p>
          Traffic to Vicero is encrypted with HTTPS. Provider API keys and channel access tokens are encrypted at rest,
          passwords are hashed, and each customer&apos;s data is isolated from every other customer&apos;s. Access to
          production systems is limited. No system is perfectly secure; if a breach affects your data we will notify the
          affected customers as the law requires.
        </p>
      </LegalSection>

      <LegalSection title="8. Your rights">
        <p>
          Depending on where you live, you can ask to access, correct or delete your personal data, to restrict or object to
          some processing, or to receive a copy. To exercise a right over a chatbot conversation, you can contact the
          business running that chatbot or write to us at <a href={`mailto:${LEGAL_EMAIL}`}>{LEGAL_EMAIL}</a>. We respond
          within 30 days.
        </p>
      </LegalSection>

      <LegalSection title="9. Children">
        <p>
          Vicero is a business tool and is not directed to children. We do not knowingly collect personal data from anyone
          under 13, or under 16 where local law sets a higher age. If you believe a child has given us data, write to us and
          we will delete it.
        </p>
      </LegalSection>

      <LegalSection title="10. International transfers, cookies and changes">
        <p>
          We are based in India, and our subprocessors may process data in other countries, including the United States and
          the European Union. Where required, we rely on contractual safeguards for those transfers.
        </p>
        <p>
          We use only the cookies needed to keep you signed in and to remember your theme preference. We do not use
          advertising or cross-site tracking cookies.
        </p>
        <p>
          We may update this policy. The &quot;Last updated&quot; date above shows the current version, and we will give
          customers notice of material changes.
        </p>
      </LegalSection>

      <LegalSection title="11. Contact">
        <p>
          {LEGAL_OPERATOR}, {LEGAL_LOCATION}
          <br />
          Email: <a href={`mailto:${LEGAL_EMAIL}`}>{LEGAL_EMAIL}</a>
        </p>
      </LegalSection>
    </LegalPage>
  );
}
