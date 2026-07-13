import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "../app/login/page";
import SignupPage from "../app/signup/page";

const routerMock = vi.hoisted(() => ({
  push: vi.fn()
}));

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock
}));

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

describe("auth pages", () => {
  beforeEach(() => {
    cleanup();
    localStorage.clear();
    routerMock.push.mockClear();
    vi.restoreAllMocks();
  });

  it("signup submits, stores token, and redirects to dashboard", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ access_token: "signup-token", token_type: "bearer" })
    );

    render(React.createElement(SignupPage));
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(localStorage.getItem("jobpilot_token")).toBe("signup-token"));
    expect(routerMock.push).toHaveBeenCalledWith("/dashboard");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/auth/signup",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("signup displays backend errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "Email already registered" }, 409)
    );

    render(React.createElement(SignupPage));
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Email already registered")).toBeInTheDocument();
  });

  it("login submits, stores token, and redirects to dashboard", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ access_token: "login-token", token_type: "bearer" })
    );

    render(React.createElement(LoginPage));
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(localStorage.getItem("jobpilot_token")).toBe("login-token"));
    expect(routerMock.push).toHaveBeenCalledWith("/dashboard");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/auth/login",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("login displays backend errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "Invalid credentials" }, 401)
    );

    render(React.createElement(LoginPage));
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByText("Invalid credentials")).toBeInTheDocument();
  });
});
