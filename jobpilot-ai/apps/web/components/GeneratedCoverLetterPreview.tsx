"use client";

import type { CoverLetterContent } from "@/lib/api";

/** Renders the structured cover letter as a clean, one-page professional letter. */
export function GeneratedCoverLetterPreview({ content }: { content: CoverLetterContent }) {
  return (
    <div className="mx-auto w-full max-w-[820px] bg-white px-12 py-11 text-[13.5px] leading-7 text-[#1c1c1c] shadow-sm ring-1 ring-black/5 print:shadow-none [font-family:Georgia,'Times_New_Roman',serif]">
      {content.date && <p>{content.date}</p>}

      <div className="mt-6">
        <p>{content.recipient || "Hiring Team"}</p>
        {content.company && <p>{content.company}</p>}
        {content.role && <p className="text-[#333]">Re: {content.role}</p>}
      </div>

      <p className="mt-6">{content.greeting || "Dear Hiring Team,"}</p>

      <div className="mt-3 grid gap-3 text-justify">
        {(content.paragraphs ?? []).map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </div>

      <div className="mt-6">
        <p>{content.closing || "Best regards,"}</p>
        <p className="mt-4 font-semibold">{content.signature}</p>
      </div>
    </div>
  );
}
