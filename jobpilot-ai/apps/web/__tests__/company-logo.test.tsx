import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { CompanyLogo } from "../components/CompanyLogo";

describe("CompanyLogo", () => {
  afterEach(cleanup);

  it("renders an img when a logo URL exists", () => {
    render(React.createElement(CompanyLogo, { company: "OpenAI", logoUrl: "https://logo.clearbit.com/openai.com" }));
    const img = screen.getByRole("img", { name: "OpenAI logo" });
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute("src", "https://logo.clearbit.com/openai.com");
  });

  it("renders a generated company mark when no real logo URL is available", () => {
    render(React.createElement(CompanyLogo, { company: "Cardinal Health", logoUrl: null }));
    expect(screen.getByRole("img", { name: "Cardinal Health generated company mark" })).toHaveTextContent("CH");
    expect(screen.getByTestId("company-logo-generated")).toBeInTheDocument();
  });

  it("never renders an aggregator-owned logo as the employer logo", () => {
    render(
      React.createElement(CompanyLogo, {
        company: "Example Commerce",
        logoUrl: (
          "https://storage.googleapis.com/simplify-imgs/companies/id/logo.png"
        )
      })
    );
    expect(screen.queryByRole("img", { name: "Example Commerce logo" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "Example Commerce generated company mark" })
    ).toHaveTextContent("EC");
  });

  it("reserves a fixed logo box so fallback does not shift the card", () => {
    const { container } = render(
      React.createElement(CompanyLogo, {
        company: "Example Commerce",
        logoUrl: null,
        size: 44
      })
    );
    const box = container.firstElementChild;
    expect(box).toHaveStyle({ width: "44px", height: "44px" });
  });

  it("falls back to a generated mark when the only real image errors", () => {
    render(React.createElement(CompanyLogo, { company: "Deepgram", logoUrl: "https://logo.clearbit.com/deepgram.com" }));
    const img = screen.getByRole("img", { name: "Deepgram logo" });
    fireEvent.error(img);
    expect(screen.getByRole("img", { name: "Deepgram generated company mark" })).toHaveTextContent("DE");
  });

  it("prefers the proxied source, and falls back to the direct logo URL if the proxy fails", () => {
    render(
      React.createElement(CompanyLogo, {
        company: "Deepgram",
        logoUrl: "https://logo.clearbit.com/deepgram.com",
        proxyPath: "/jobs/companies/deepgram/logo"
      })
    );
    const first = screen.getByRole("img", { name: "Deepgram logo" });
    expect(first).toHaveAttribute("src", expect.stringContaining("/jobs/companies/deepgram/logo"));
    fireEvent.error(first);
    const second = screen.getByRole("img", { name: "Deepgram logo" });
    expect(second).toHaveAttribute("src", "https://logo.clearbit.com/deepgram.com");
  });

  it("never shows a broken-image icon: after every source fails, a generated mark remains", () => {
    render(
      React.createElement(CompanyLogo, {
        company: "Deepgram",
        logoUrl: "https://logo.clearbit.com/deepgram.com",
        proxyPath: "/jobs/companies/deepgram/logo"
      })
    );
    fireEvent.error(screen.getByRole("img", { name: "Deepgram logo" }));
    fireEvent.error(screen.getByRole("img", { name: "Deepgram logo" }));
    expect(screen.getByRole("img", { name: "Deepgram generated company mark" })).toHaveTextContent("DE");
    expect(screen.getByTestId("company-logo-generated")).toBeInTheDocument();
  });
});
