"use client";

import type { ResumeContent } from "@/lib/api";

/**
 * Renders the structured resume content as a clean, ATS-friendly document page:
 * single column, standard section names, simple bullets, no icons/tables/colors.
 */
export function GeneratedResumePreview({ content }: { content: ResumeContent }) {
  const header = content.header ?? { full_name: "", email: "", phone: "", location: "", links: [] };
  const contact = [header.email, header.phone, header.location, ...(header.links ?? [])].filter(Boolean);

  return (
    <div className="mx-auto w-full max-w-[820px] bg-white px-10 py-9 text-[13px] leading-relaxed text-[var(--text-primary)] shadow-sm ring-1 ring-black/5 print:shadow-none [font-family:Georgia,'Times_New_Roman',serif]">
      <header className="border-b border-[var(--text-primary)] pb-3 text-center">
        <h1 className="text-2xl font-bold uppercase tracking-wide">{header.full_name || "Your Name"}</h1>
        {contact.length > 0 && (
          <p className="mt-1 text-[12px] text-[var(--text-secondary)]">{contact.join("  |  ")}</p>
        )}
      </header>

      {content.summary && (
        <Section title="Professional Summary">
          <p>{content.summary}</p>
        </Section>
      )}

      {content.skills?.some((g) => g.items?.length) && (
        <Section title="Core Skills">
          <div className="grid gap-1">
            {content.skills
              .filter((g) => g.items?.length)
              .map((group, index) => (
                <p key={index}>
                  {group.category && <span className="font-semibold">{group.category}: </span>}
                  {group.items.join(", ")}
                </p>
              ))}
          </div>
        </Section>
      )}

      {content.experience?.length > 0 && (
        <Section title="Professional Experience">
          {content.experience.map((exp, index) => (
            <div key={index} className="mb-2.5 last:mb-0">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                <p className="font-bold">
                  {exp.title}
                  {exp.company ? ` — ${exp.company}` : ""}
                </p>
                {exp.dates && <p className="text-[12px] text-[var(--text-secondary)]">{exp.dates}</p>}
              </div>
              {exp.location && <p className="text-[12px] italic text-[var(--text-secondary)]">{exp.location}</p>}
              <BulletList items={exp.bullets} />
            </div>
          ))}
        </Section>
      )}

      {content.projects?.length > 0 && (
        <Section title="Selected Projects">
          {content.projects.map((proj, index) => (
            <div key={index} className="mb-2.5 last:mb-0">
              <p className="font-bold">
                {proj.name}
                {proj.technologies?.length ? (
                  <span className="font-normal text-[var(--text-secondary)]"> — {proj.technologies.join(", ")}</span>
                ) : null}
              </p>
              <BulletList items={proj.bullets} />
            </div>
          ))}
        </Section>
      )}

      {content.education?.length > 0 && (
        <Section title="Education">
          {content.education.map((edu, index) => (
            <div key={index} className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3">
              <p>
                <span className="font-semibold">{edu.school}</span>
                {edu.degree ? `, ${edu.degree}` : ""}
                {edu.details ? ` — ${edu.details}` : ""}
              </p>
              {edu.dates && <p className="text-[12px] text-[var(--text-secondary)]">{edu.dates}</p>}
            </div>
          ))}
        </Section>
      )}

      {[...(content.awards ?? []), ...(content.certifications ?? [])].filter((a) => a?.name).length > 0 && (
        <Section title="Awards & Certifications">
          <ul className="list-disc pl-5">
            {[...(content.awards ?? []), ...(content.certifications ?? [])]
              .filter((a) => a?.name)
              .map((award, index) => (
                <li key={index}>{award.name}</li>
              ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-4">
      <h2 className="mb-1.5 border-b border-[var(--text-muted)] pb-0.5 text-[12px] font-bold uppercase tracking-wide text-[var(--text-primary)]">
        {title}
      </h2>
      {children}
    </section>
  );
}

function BulletList({ items }: { items: string[] }) {
  if (!items?.length) {
    return null;
  }
  return (
    <ul className="mt-1 list-disc pl-5">
      {items.map((item, index) => (
        <li key={index} className="mb-0.5">
          {item}
        </li>
      ))}
    </ul>
  );
}
