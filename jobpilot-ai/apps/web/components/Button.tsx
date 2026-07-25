import clsx from "clsx";

export function Button({
  children,
  className,
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger";
}) {
  return (
    <button
      className={clsx(
        "focus-ring inline-flex h-10 items-center justify-center gap-2 rounded-md px-4 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-pine text-white hover:bg-[var(--accent-hover)]",
        variant === "secondary" && "border border-line bg-white text-ink hover:bg-panel",
        variant === "danger" && "bg-coral text-white hover:bg-[var(--danger)]",
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}

