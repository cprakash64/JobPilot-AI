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

  it("falls back to the neutral placeholder (never an initial letter) when no logo URL is provided", () => {
    render(React.createElement(CompanyLogo, { company: "Cardinal Health", logoUrl: null }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByTestId("company-logo-placeholder")).toBeInTheDocument();
    expect(screen.queryByText("CH")).not.toBeInTheDocument();
  });

  it("tries the secondary source before falling back to the neutral placeholder when the primary image errors", () => {
    render(React.createElement(CompanyLogo, { company: "Deepgram", logoUrl: "https://logo.clearbit.com/deepgram.com" }));
    const img = screen.getByRole("img", { name: "Deepgram logo" });
    fireEvent.error(img);
    // No secondary source was provided, so it falls straight through to the
    // placeholder — never an initial, never a broken-image icon.
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByTestId("company-logo-placeholder")).toBeInTheDocument();
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

  it("never shows a broken-image icon: after every source fails, only the placeholder remains", () => {
    render(
      React.createElement(CompanyLogo, {
        company: "Deepgram",
        logoUrl: "https://logo.clearbit.com/deepgram.com",
        proxyPath: "/jobs/companies/deepgram/logo"
      })
    );
    fireEvent.error(screen.getByRole("img", { name: "Deepgram logo" }));
    fireEvent.error(screen.getByRole("img", { name: "Deepgram logo" }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByTestId("company-logo-placeholder")).toBeInTheDocument();
  });
});
