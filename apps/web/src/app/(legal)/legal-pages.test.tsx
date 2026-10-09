import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PrivacyPage from "./privacy/page";
import TermsPage from "./terms/page";
import DataDeletionPage from "./data-deletion/page";
import { LEGAL_EMAIL } from "@/lib/legal";

// Each page is a server component that needs no session: it renders with nothing but its props.
describe("public legal pages", () => {
  it("privacy has a single h1 and the contact address", () => {
    render(<PrivacyPage />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Privacy Policy" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: LEGAL_EMAIL }).length).toBeGreaterThan(0);
  });

  it("terms has a single h1", () => {
    render(<TermsPage />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Terms of Service" })).toBeInTheDocument();
  });

  it("data deletion renders its instructions without a confirmation code", async () => {
    render(await DataDeletionPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Data Deletion Instructions" })).toBeInTheDocument();
    expect(screen.queryByText(/Deletion request status/)).not.toBeInTheDocument();
  });

  it("data deletion reports a malformed code as unknown without calling the API", async () => {
    render(await DataDeletionPage({ searchParams: Promise.resolve({ code: "../x" }) }));
    expect(screen.getByRole("status")).toHaveTextContent(/No request was found/);
  });
});
