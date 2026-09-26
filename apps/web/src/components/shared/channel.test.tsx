import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { channelMeta, channelTone } from "@/lib/channel-meta";
import { ChannelBadge, ChannelDot, ChannelIcon, ChannelText } from "./channel";

const KEYS: [string, string, string][] = [
  ["widget", "Web Chat", "widget"],
  ["web", "Web", "widget"],
  ["whatsapp", "WhatsApp", "whatsapp"],
  ["instagram", "Instagram", "instagram"],
  ["facebook", "Messenger", "facebook"],
  ["telegram", "Telegram", "telegram"],
  ["email", "Email", "email"],
  ["slack", "Slack", "slack"],
  ["discord", "Discord", "discord"],
];

describe("channelTone", () => {
  for (const [key, , color] of KEYS) {
    it(`${key} uses the ${color} colour set`, () => {
      const t = channelTone(key);
      expect(t.dot).toBe(`rgb(var(--ch-${color}))`);
      expect(t.text).toBe(`rgb(var(--ch-${color}-text))`);
      expect(t.soft).toBe(`rgb(var(--ch-${color}-soft))`);
    });
  }

  it("only Instagram carries the brand gradient", () => {
    expect(channelTone("instagram").gradient).toBe("var(--ch-instagram-gradient)");
    expect(channelTone("whatsapp").gradient).toBeUndefined();
  });

  it("gives unknown and non-brand channels neutral greys", () => {
    for (const key of ["carrier-pigeon", "playground", "dashboard", ""]) {
      expect(channelTone(key).dot, key).toBe("rgb(var(--faint))");
      expect(channelTone(key).soft, key).toBe("rgb(var(--surface-2))");
    }
  });
});

describe("<ChannelBadge>", () => {
  for (const [key, label] of KEYS) {
    it(`labels ${key} as "${label}" in that channel's colours`, () => {
      render(<ChannelBadge channel={key} />);
      const badge = screen.getByText(label);
      expect(badge).toHaveStyle({ color: channelTone(key).text });
    });
  }

  it("falls back to 'Other' for an unknown channel", () => {
    render(<ChannelBadge channel="carrier-pigeon" />);
    expect(screen.getByText("Other")).toBeInTheDocument();
  });

  it("agrees with channelMeta on every label", () => {
    for (const [key, label] of KEYS) expect(channelMeta(key).label).toBe(label);
  });
});

describe("other channel pieces", () => {
  it("<ChannelDot> is decorative and paints the Instagram gradient", () => {
    const { container } = render(<ChannelDot channel="instagram" />);
    const dot = container.firstElementChild as HTMLElement;
    expect(dot).toHaveAttribute("aria-hidden", "true");
    expect(dot.style.backgroundImage).toContain("--ch-instagram-gradient");
  });

  it("<ChannelIcon> is hidden from assistive tech unless labelled", () => {
    const { container, rerender } = render(<ChannelIcon channel="whatsapp" />);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
    rerender(<ChannelIcon channel="whatsapp" label="WhatsApp" />);
    expect(screen.getByRole("img", { name: "WhatsApp" })).toBeInTheDocument();
  });

  it("<ChannelText> defaults to the channel label and accepts an override", () => {
    const { rerender } = render(<ChannelText channel="telegram" />);
    expect(screen.getByText("Telegram")).toHaveStyle({ color: channelTone("telegram").text });
    rerender(<ChannelText channel="telegram">via Telegram</ChannelText>);
    expect(screen.getByText("via Telegram")).toBeInTheDocument();
  });
});
