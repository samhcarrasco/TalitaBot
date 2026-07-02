# Talita's Job Bot 💼

This little program logs into **your** LinkedIn, finds marketing jobs for you, and
fills out the "Easy Apply" forms automatically. This page tells you exactly how to
start it. No tech knowledge needed — just follow the steps in order.

> 💡 **Don't panic at the word "terminal."** It's just a little box where you type a
> line and press Enter. You'll only touch it a few times. If any step gives a red
> error you don't understand, take a screenshot and send it to Sam.

---

## Part 1 — First-time setup (do this ONCE, ~20 minutes)

You only ever do Part 1 one time on this computer. After that you skip straight to Part 3.

### Step 1 — Open the project in VS Code
1. Open **VS Code**.
2. Top menu: **File → Open Folder…**
3. Choose the **`talitaBot`** folder and click **Select Folder**.
4. On the left you should now see a list of files (README.md, config, data, etc.).

### Step 2 — Open the terminal
- Top menu: **Terminal → New Terminal**.
- A panel opens at the **bottom** of VS Code. That's the terminal. You'll type lines
  here and press **Enter** after each one.

### Step 3 — Set up the bot
Type these two lines into the terminal **one at a time**. After each one press
**Enter** and **wait** until it finishes and you get a fresh line before the next.

**Line 1 — download everything the bot needs (takes a few minutes):**
```
uv sync
```

**Line 2 — install the browser the bot drives:**
```
uv run playwright install chromium
```

✅ When both finish without a red error, Part 1 is done forever.

---

## Part 2 — Add your info (do this ONCE)

### Step 4 — Your LinkedIn login + your API key
1. In the file list on the left, click the file named **`.env`** to open it.
2. Inside, each line looks like `name="value"`. You only change the text **between
   the quotes** — leave the name and the `=` and the quotes alone.
3. Change **exactly these three lines**. When you open the file they look like this:

   ```
   linkedin_email="your_linkedin_email@example.com"
   linkedin_password="your_linkedin_password"
   llm_api_key="your_llm_api_key"
   ```

   Replace the placeholder text so they become **your** details, for example:

   ```
   linkedin_email="talita@gmail.com"
   linkedin_password="myLinkedInPassword"
   llm_api_key="sk-abc123...your real key..."
   ```

   👉 **Your API key goes on the `llm_api_key` line**, pasted between the quotes.
4. **Don't touch any other line** — leave everything else exactly as it is.
5. Save with **Ctrl + S**.

> 🔒 This file stays on your computer only — your password and key are never uploaded
> anywhere (it's in the "ignore" list, so it never leaves your machine).

### Step 5 — Your resume
1. Have your resume ready as a **PDF**.
2. In the file list, open the **`data`** folder, then the **`resumes`** folder.
3. **Drag your PDF** into that `resumes` folder.
4. **Rename it exactly** to: `Resume_Talita.pdf`
   (right-click the file → Rename). The name must match exactly, capital R.

---

## Part 3 — Run it (this is your everyday step) ▶️

**The easy way:** in the file list, find **`start.cmd`** and **double-click** it.
A black window opens and the bot starts.

*(Or, if you prefer the terminal: type `uv run python main.py` and press Enter.)*

Then:
1. A **Chrome window opens by itself.**
2. **The very first time only:** log into LinkedIn yourself in that window (type your
   code / do the phone verification if it asks). After this first login it stays logged
   in and won't ask again.
3. Sit back — it starts finding marketing jobs and applying. It **stops automatically
   after 30 applications**.

### To stop it early
- Just **close the Chrome window**, or click the black window and press **Ctrl + C**.
- To **pause and resume**: press **Ctrl + X** (give it a couple seconds to react).

---

## What to expect (so nothing surprises you)

- ✅ It applies only to **entry / associate-level marketing & communications** jobs.
- ⏭️ It **skips** jobs that make you write an essay ("Why do you want to work here?",
  cover letters, "Tell us about yourself"). That's on purpose — the bot won't fake those
  in your voice. So it will pass on some jobs, and that's normal.
- 💰 On salary questions it answers around **$65,000–$75,000** (or matches the job's own
  listed range). There's no minimum, so it won't skip low-paying jobs.
- 🔁 Run it again the next day for a fresh batch (LinkedIn resets the daily limit).

---

## If something goes wrong

1. Take a screenshot of the black window / terminal (especially the last few red lines).
2. Send it to **Sam**.

That's it — you don't need to fix anything technical yourself. 🙂

---

<sub>Technical setup details for engineers live in [SETUP.md](SETUP.md).</sub>
