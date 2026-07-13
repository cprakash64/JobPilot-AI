/** Local ATS-like form fixtures for unit tests. No live sites are ever used. */

export const GREENHOUSE_FIXTURE = `
  <div id="grnhse_app">
    <form id="application_form">
      <label for="first_name">First Name *</label>
      <input id="first_name" name="job_application[first_name]" type="text" required />

      <label for="last_name">Last Name *</label>
      <input id="last_name" name="job_application[last_name]" type="text" required />

      <label for="email">Email *</label>
      <input id="email" name="job_application[email]" type="email" autocomplete="email" required />

      <label for="phone">Phone</label>
      <input id="phone" name="job_application[phone]" type="tel" />

      <label for="linkedin">LinkedIn Profile</label>
      <input id="linkedin" name="job_application[linkedin]" type="url" />

      <label for="resume">Resume/CV *</label>
      <input id="resume" name="job_application[resume]" type="file" required />

      <label for="cover">Cover Letter</label>
      <input id="cover" name="job_application[cover_letter]" type="file" />

      <label for="work_auth">Are you legally authorized to work in the US?</label>
      <select id="work_auth" name="job_application[work_authorization]">
        <option value="">Select...</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>

      <label for="why">Why do you want to work here?</label>
      <textarea id="why" name="job_application[why]"></textarea>

      <fieldset>
        <legend>Voluntary Self-Identification</legend>
        <label for="gender">Gender</label>
        <select id="gender" name="job_application[gender]">
          <option value="">Decline to self-identify</option>
          <option value="f">Female</option>
          <option value="m">Male</option>
        </select>
      </fieldset>

      <input type="text" name="honeypot_leave_blank" style="left:-9999px" />
      <input id="disabled_field" name="disabled_field" type="text" disabled />

      <button id="submit_app" type="submit">Submit Application</button>
    </form>
  </div>
`;

export const LEVER_FIXTURE = `
  <form class="application-form" data-qa="application-form">
    <input name="name" placeholder="Full name" type="text" autocomplete="name" />
    <input name="email" placeholder="Email" type="email" />
    <input name="phone" placeholder="Phone" type="tel" />
    <input name="urls[LinkedIn]" placeholder="LinkedIn URL" type="url" />
    <input name="resume" type="file" />
    <textarea name="comments" placeholder="Additional information"></textarea>
    <button class="template-btn-submit" type="submit">Submit application</button>
  </form>
`;

export const ASHBY_FIXTURE = `
  <div class="ashby-application-form-container">
    <form class="_form_abc">
      <label for="name">Name</label>
      <input id="name" name="name" type="text" />
      <label for="email">Email</label>
      <input id="email" name="email" type="email" />
      <label for="school">Which school did you attend?</label>
      <input id="school" name="education" type="text" />
      <button type="submit">Submit</button>
    </form>
  </div>
`;

export const GENERIC_FIXTURE = `
  <form>
    <label for="fullname">Your name</label>
    <input id="fullname" name="fullname" type="text" />
    <label for="mail">E-mail address</label>
    <input id="mail" name="mail" type="text" />
    <label for="city">City</label>
    <input id="city" name="city" type="text" />
    <button type="submit">Apply</button>
  </form>
`;

export const EEO_FIXTURE = `
  <form>
    <label for="veteran">Protected Veteran Status</label>
    <select id="veteran" name="veteran"><option value="">Choose</option><option>I am a veteran</option></select>
    <label for="disability">Do you have a disability?</label>
    <select id="disability" name="disability"><option value="">Choose</option><option>Yes</option></select>
    <label for="criminal">Have you ever been convicted of a felony?</label>
    <input id="criminal" name="criminal" type="text" />
    <label for="salhist">What is your current salary?</label>
    <input id="salhist" name="salary_history" type="text" />
  </form>
`;

export const MULTISTEP_STEP2_FIXTURE = `
  <form>
    <h2>Step 2 of 3: Experience</h2>
    <label for="company">Current Company</label>
    <input id="company" name="company" type="text" />
    <label for="title">Current Title</label>
    <input id="title" name="title" type="text" />
    <label for="years">Years of experience</label>
    <input id="years" name="years" type="number" />
    <button type="button">Back</button>
    <button type="button">Next</button>
  </form>
`;

/**
 * Sanitized approximation of Temporal's live Ashby application form
 * (jobs.ashbyhq.com/temporal/…/application). Field names/labels mirror the real
 * page structure; no personal data. Covers the fields the reproduction listed:
 * name, email, phone, resume + cover-letter uploads, LinkedIn, website, plus a
 * screening radio, a sensitive demographic select, and a legal attestation.
 */
export const TEMPORAL_ASHBY_FIXTURE = `
  <div class="ashby-application-form-container">
    <form class="_form_xyz1" aria-label="Application">
      <div class="_fieldEntry_abc">
        <label for="_systemfield_name">Name<span aria-hidden="true">*</span></label>
        <input id="_systemfield_name" name="_systemfield_name" type="text" autocomplete="name" required />
      </div>
      <div class="_fieldEntry_abc">
        <label for="_systemfield_email">Email<span aria-hidden="true">*</span></label>
        <input id="_systemfield_email" name="_systemfield_email" type="email" autocomplete="email" required />
      </div>
      <div class="_fieldEntry_abc">
        <label for="_systemfield_phone">Phone</label>
        <input id="_systemfield_phone" name="_systemfield_phone" type="tel" autocomplete="tel" />
      </div>
      <div class="_fieldEntry_abc">
        <label for="_systemfield_resume">Resume<span aria-hidden="true">*</span></label>
        <input id="_systemfield_resume" name="_systemfield_resume" type="file" accept=".pdf,.doc,.docx" required />
      </div>
      <div class="_fieldEntry_abc">
        <label for="cover_letter">Cover letter</label>
        <input id="cover_letter" name="cover_letter" type="file" accept=".pdf,.doc,.docx" />
      </div>
      <div class="_fieldEntry_abc">
        <label for="q_linkedin">LinkedIn Profile</label>
        <input id="q_linkedin" name="q_linkedin" type="url" placeholder="https://linkedin.com/in/…" />
      </div>
      <div class="_fieldEntry_abc">
        <label for="q_website">Website</label>
        <input id="q_website" name="q_website" type="url" placeholder="https://" />
      </div>
      <fieldset class="_fieldEntry_abc">
        <legend>Are you legally authorized to work in the United States?</legend>
        <label><input type="radio" name="q_work_auth" value="Yes" /> Yes</label>
        <label><input type="radio" name="q_work_auth" value="No" /> No</label>
      </fieldset>
      <fieldset class="_fieldEntry_abc">
        <legend>Voluntary Self-Identification — Gender</legend>
        <select id="q_gender" name="q_gender">
          <option value="">Decline to self-identify</option>
          <option value="female">Female</option>
          <option value="male">Male</option>
          <option value="nonbinary">Non-binary</option>
        </select>
      </fieldset>
      <div class="_fieldEntry_abc">
        <label><input type="checkbox" id="q_attest" name="q_attest" /> I certify that the information provided is accurate.</label>
      </div>
      <!-- Ashby honeypot / hidden anti-spam field -->
      <input type="text" name="_hp_email" tabindex="-1" style="position:absolute;left:-9999px" />
      <button type="submit" class="_submitButton_xyz">Submit Application</button>
    </form>
  </div>
`;

/** Build a DiscoveredField-friendly document body from a fixture string. */
export function mountFixture(html: string): Document {
  document.body.innerHTML = html;
  return document;
}
