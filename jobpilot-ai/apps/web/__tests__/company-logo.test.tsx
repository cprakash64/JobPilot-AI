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

  it("falls back to the initial when no logo URL is provided", () => {
    render(React.createElement(CompanyLogo, { company: "Cardinal Health", logoUrl: null }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("CH")).toBeInTheDocument();
  });

  it("falls back to the initial when the image errors", () => {
    render(React.createElement(CompanyLogo, { company: "Deepgram", logoUrl: "https://logo.clearbit.com/deepgram.com" }));
    const img = screen.getByRole("img", { name: "Deepgram logo" });
    fireEvent.error(img);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("D")).toBeInTheDocument();
  });
});
