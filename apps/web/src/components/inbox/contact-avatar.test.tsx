import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ContactAvatar, contactHandle, contactInitials, contactLabel } from "./contact-avatar";

describe("contactLabel", () => {
  it("prefers the contact's real name", () => {
    expect(contactLabel({ display_name: "Aadesh" }, "IG42")).toBe("Aadesh");
  });

  it("falls back to the end of the channel id when there's no contact yet", () => {
    expect(contactLabel(null, "178414000000000000999")).toBe("…00000999");
    expect(contactLabel(null, "17841400000000000")).toBe("17841400000000000"); // exactly at the limit
    expect(contactLabel(null, "55")).toBe("55");
    expect(contactLabel(null, null)).toBe("Unknown contact");
  });
});

describe("contactInitials / contactHandle", () => {
  it("takes the first and last word's first letter", () => {
    expect(contactInitials("Aadesh")).toBe("A");
    expect(contactInitials("aadesh kumar s")).toBe("AS");
    expect(contactInitials("  மதி  ")).toBe("ம");
  });

  it("gives nothing for ids and placeholders", () => {
    expect(contactInitials(null)).toBe("");
    expect(contactInitials("Unknown contact")).toBe("");
    expect(contactInitials("…00000999")).toBe("");
    expect(contactInitials("17841400")).toBe("");
  });

  it("formats an @username and tolerates a stored @", () => {
    expect(contactHandle({ username: "_.aadesx._" })).toBe("@_.aadesx._");
    expect(contactHandle({ username: "@acme" })).toBe("@acme");
    expect(contactHandle({ username: "" })).toBeNull();
    expect(contactHandle(null)).toBeNull();
  });
});

describe("ContactAvatar", () => {
  it("renders the photo when there is one", () => {
    const { container } = render(
      <ContactAvatar channel="instagram" name="Aadesh" avatarUrl="https://cdn/x.jpg" />,
    );
    expect(container.querySelector("img")).toHaveAttribute("src", "https://cdn/x.jpg");
  });

  it("names the contact and their platform for screen readers", () => {
    render(<ContactAvatar channel="instagram" name="Aadesh" />);
    expect(screen.getByText("Aadesh on Instagram")).toBeInTheDocument();
  });

  it("shows initials when the platform gives no avatar", () => {
    const { container } = render(<ContactAvatar channel="whatsapp" name="Rohak" />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("R")).toBeInTheDocument();
    expect(screen.getByText("Rohak on WhatsApp")).toBeInTheDocument();
  });

  it("falls back to a person glyph when there is no name either", () => {
    const { container } = render(<ContactAvatar channel="whatsapp" name="…00000999" />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg.lucide-user-round")).not.toBeNull();
  });
});
