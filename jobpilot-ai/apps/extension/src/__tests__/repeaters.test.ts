import { beforeEach, describe, expect, it } from "vitest";
import { fillStructuredRepeaters } from "../fields/repeaters";
import type { ApplicationSessionData } from "../types";

function monthOptions(): string {
  return ["Select…", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    .map((value) => `<option>${value}</option>`).join("");
}

function employment(index: number): string {
  return `<section data-job="${index}">
    <label>Company name *<input name="company_${index}"></label>
    <label>Title *<input name="title_${index}"></label>
    <label>Location<input name="location_${index}"></label>
    <label>Start date month *<select name="start_month_${index}">${monthOptions()}</select></label>
    <label>Start date year *<input name="start_year_${index}"></label>
    <label>End date month *<select name="end_month_${index}">${monthOptions()}</select></label>
    <label>End date year *<input name="end_year_${index}"></label>
    <label>Current role<input type="checkbox" name="current_${index}"></label>
  </section>`;
}

function education(index: number): string {
  return `<section data-school="${index}">
    <label>School<input name="school_${index}"></label>
    <label>Degree<select name="degree_${index}">
      <option>Select…</option><option>Bachelor's Degree</option><option>Master's Degree</option>
    </select></label>
    <label>Discipline<input name="major_${index}"></label>
  </section>`;
}

function session(): ApplicationSessionData {
  return {
    sessionId: 91,
    atsType: "greenhouse",
    officialUrl: "https://job-boards.greenhouse.io/lyft/jobs/1",
    jobTitle: "Engineer",
    company: "Lyft",
    answers: [],
    unresolvedQuestions: [],
    profileData: {
      experience: [
        { company: "VeoTrex", title: "Software Engineer", location: "Tempe, Arizona", start_date: "2024-01-01", currently_working: true },
        { company: "Earlier Co", title: "Developer", location: "Phoenix, Arizona", start_date: "2021-06-01", end_date: "2023-12-01", currently_working: false }
      ],
      education: [
        { school: "Arizona State University", degree: "Bachelor of Science", major: "Computer Science" },
        { school: "Mesa Community College", degree: "Master of Science", major: "Data Science" }
      ]
    }
  };
}

describe("structured profile repeaters", () => {
  beforeEach(() => {
    document.body.innerHTML = `<form>
      <div class="section-title">Employment</div><div id="jobs">${employment(0)}</div>
      <button type="button" id="add-job">Add another</button>
      <div class="section-title">Education</div><div id="schools">${education(0)}</div>
      <button type="button" id="add-school">Add another</button>
      <h2>Application Questions</h2><label>May we contact your current employer?<select><option>Select…</option><option>Yes</option><option>No</option></select></label>
    </form>`;
    document.querySelector("#add-job")!.addEventListener("click", () => {
      const jobs = document.querySelector("#jobs")!;
      jobs.insertAdjacentHTML("beforeend", employment(jobs.children.length));
    });
    document.querySelector("#add-school")!.addEventListener("click", () => {
      const schools = document.querySelector("#schools")!;
      schools.insertAdjacentHTML("beforeend", education(schools.children.length));
    });
  });

  it("adds and fills every saved employment and education record by index", async () => {
    const results = await fillStructuredRepeaters(document.querySelector("form")!, session());

    expect(results).toHaveLength(2);
    expect(results.every((result) => result.recordsFound === 2)).toBe(true);
    expect(results.flatMap((result) => result.failures)).toEqual([]);
    expect(document.querySelectorAll("[data-job]")).toHaveLength(2);
    expect(document.querySelectorAll("[data-school]")).toHaveLength(2);

    expect((document.querySelector('[name="company_0"]') as HTMLInputElement).value).toBe("VeoTrex");
    expect((document.querySelector('[name="title_1"]') as HTMLInputElement).value).toBe("Developer");
    expect((document.querySelector('[name="start_month_1"]') as HTMLSelectElement).value).toBe("June");
    expect((document.querySelector('[name="end_year_1"]') as HTMLInputElement).value).toBe("2023");
    expect((document.querySelector('[name="current_0"]') as HTMLInputElement).checked).toBe(true);
    expect((document.querySelector('[name="school_0"]') as HTMLInputElement).value).toBe("Arizona State University");
    expect((document.querySelector('[name="degree_0"]') as HTMLSelectElement).value).toBe("Bachelor's Degree");
    expect((document.querySelector('[name="degree_1"]') as HTMLSelectElement).value).toBe("Master's Degree");
    expect((document.querySelector('[name="major_1"]') as HTMLInputElement).value).toBe("Data Science");

    for (const element of Array.from(document.querySelectorAll("[data-job] input, [data-job] select, [data-school] input, [data-school] select"))) {
      if ((element as HTMLInputElement).value && (element as HTMLInputElement).value !== "Select…") {
        expect(element.getAttribute("data-jobpilot-repeater")).toBe("1");
      }
    }
  });

  it("reports when an add-another control cannot create every saved row", async () => {
    document.querySelector("#add-job")?.remove();
    const results = await fillStructuredRepeaters(document.querySelector("form")!, session());
    const employmentResult = results[0];

    expect(employmentResult.recordsRequested).toBe(2);
    expect(employmentResult.recordsFound).toBe(1);
    expect(employmentResult.failures).toContain(
      "Employment: found 1 of 2 saved record rows"
    );
  });
});
