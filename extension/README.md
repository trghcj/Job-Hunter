# JobHunter bubble (Chrome extension)

A small floating bubble on job pages. When you open a job on LinkedIn, Naukri, Indeed or a
company careers page (Greenhouse, Lever, Ashby, Workday, SmartRecruiters), it shows whether
the job matches your JobHunter filters, lets you save it to My jobs, and lists your latest
matches.

It only reads the job page you have open, in your own browser. It never clicks, scrolls,
opens pages or searches for you, and never sends anything to LinkedIn, Naukri or Indeed.

## Install (developer mode)

1. Chrome → `chrome://extensions` → switch on **Developer mode**.
2. **Load unpacked** → choose this `extension` folder.
3. JobHunter → Settings → **Connect extension** → **Connect this browser**.

## Tests

```powershell
cd extension
npm install   # once: installs linkedom, used only by the tests
npm test
```

Icons are made from the web app's logo: `python extension/tools/make_icons.py`.
