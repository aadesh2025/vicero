import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { CodeBlock } from "./code-block";
import { CodeTabs } from "./code-tabs";

const CURL = 'curl https://YOUR_API_HOST/v1/agents \\\n  -H "Authorization: Bearer YOUR_API_KEY"';
const JS = 'fetch(url, { headers: { Authorization: "Bearer YOUR_API_KEY" } })';
const PY = 'requests.get(url, headers={"Authorization": "Bearer YOUR_API_KEY"})';

let written: string[];
let setItem: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  written = [];
  Object.assign(navigator, { clipboard: { writeText: vi.fn(async (t: string) => void written.push(t)) } });
  // The docs must never write to the reader's browser storage.
  setItem = vi.spyOn(Storage.prototype, "setItem");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function examples() {
  return (
    <CodeTabs labels="cURL,JavaScript,Python">
      <CodeBlock>
        <code>{CURL}</code>
      </CodeBlock>
      <CodeBlock>
        <code>{JS}</code>
      </CodeBlock>
      <CodeBlock>
        <code>{PY}</code>
      </CodeBlock>
    </CodeTabs>
  );
}

describe("CodeTabs", () => {
  it("shows the first language and hides the others", () => {
    render(examples());
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["cURL", "JavaScript", "Python"]);
    expect(screen.getByRole("tab", { name: "cURL" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("curl");
  });

  it("switches language on click", () => {
    render(examples());
    fireEvent.click(screen.getByRole("tab", { name: "Python" }));
    expect(screen.getByRole("tab", { name: "Python" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("requests.get");
  });

  it("keeps every example in the page, hidden rather than removed", () => {
    // Search, print and no-JavaScript readers all rely on the inactive panels still being there.
    const { container } = render(examples());
    expect(container.querySelectorAll('[role="tabpanel"]').length).toBe(3);
    expect(container.textContent).toContain("requests.get");
  });

  it("moves between tabs with the arrow keys, wrapping at the ends", () => {
    render(examples());
    const list = screen.getByRole("tablist");
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "JavaScript" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(list, { key: "ArrowLeft" });
    fireEvent.keyDown(list, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Python" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(list, { key: "Home" });
    expect(screen.getByRole("tab", { name: "cURL" })).toHaveAttribute("aria-selected", "true");
  });

  it("has one copy button for the whole group, not one per example", () => {
    render(examples());
    expect(screen.getAllByRole("button", { name: /^Copy/ })).toHaveLength(1);
  });

  it("copies the example that is showing, exactly as displayed — placeholder and all", async () => {
    render(examples());
    fireEvent.click(screen.getByRole("button", { name: /Copy cURL example/ }));
    await waitFor(() => expect(written).toHaveLength(1));
    expect(written[0]).toBe(CURL);
    expect(written[0]).toContain("YOUR_API_KEY");

    fireEvent.click(screen.getByRole("tab", { name: "Python" }));
    fireEvent.click(screen.getByRole("button", { name: /Copy Python example/ }));
    await waitFor(() => expect(written).toHaveLength(2));
    expect(written[1]).toBe(PY);
  });

  it("never writes to localStorage or sessionStorage, whatever the reader does", async () => {
    render(examples());
    fireEvent.click(screen.getByRole("tab", { name: "JavaScript" }));
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "End" });
    fireEvent.click(screen.getByRole("button", { name: /Copy/ }));
    await waitFor(() => expect(written).toHaveLength(1));
    expect(setItem).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("does not claim success when the browser refuses clipboard access", async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    render(examples());
    fireEvent.click(screen.getByRole("button", { name: /Copy cURL example/ }));
    // Still labelled "Copy", not "Copied" — silence is better than a false confirmation.
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByRole("button", { name: /Copy cURL example/ })).toBeInTheDocument();
  });

  it("labels an example that has no matching label rather than dropping it", () => {
    render(
      <CodeTabs labels="cURL">
        <CodeBlock>
          <code>a</code>
        </CodeBlock>
        <CodeBlock>
          <code>b</code>
        </CodeBlock>
      </CodeTabs>,
    );
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["cURL", "Example 2"]);
  });
});

describe("CodeBlock on its own", () => {
  it("has a copy button that copies its text", async () => {
    render(
      <CodeBlock>
        <code>{"Authorization: Bearer YOUR_API_KEY"}</code>
      </CodeBlock>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    await waitFor(() => expect(written).toEqual(["Authorization: Bearer YOUR_API_KEY"]));
    expect(setItem).not.toHaveBeenCalled();
  });
});
