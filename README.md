# Talita's Job Bot 💼

This program uses your LinkedIn. It looks for marketing jobs. It fills the
"Easy Apply" forms for you. It sends them for you.

This page shows you how to start it. Follow the steps in order, from top to bottom.

Do not worry. You do not need to know about computers. If you see red words (an
error) that you do not understand, take a photo of the screen and send it to Sam.

---

## Part 1 — First time only (you do this ONE time)

You do Part 1 only one time on this computer. After that, you go straight to Part 3.

### Step 1 — Open the folder
1. Open the program **VS Code**.
2. At the top, click **File**. Then click **Open Folder**.
3. Find the folder named **talitaBot**. Click it. Then click **Select Folder**.
4. On the left side, you now see a list of files.

### Step 2 — Open the typing box
- At the top, click **Terminal**. Then click **New Terminal**.
- A box opens at the bottom. This is where you type.
- After you type a line, press the **Enter** key.

### Step 3 — Get the bot ready
Type the two lines below. Type one line. Press **Enter**. Wait until it is done.
Then type the next line.

Line 1 (this takes a few minutes):
```
uv sync
```

Line 2:
```
uv run playwright install chromium
```

When both are done and there are no red words, Part 1 is finished for ever.

---

## Part 2 — Add your things (you do this ONE time)

### Step 4 — Your LinkedIn login and your key
1. On the left, click the file named **.env**. It opens.
2. Every line looks like this: `name="value"`. You only change the words **inside
   the " "**. Do not change anything else.
3. Change these three lines. Now they look like this:
   ```
   linkedin_email="your_linkedin_email@example.com"
   linkedin_password="your_linkedin_password"
   llm_api_key="your_llm_api_key"
   ```
   Put your own words inside the " ". Like this:
   ```
   linkedin_email="talita@gmail.com"
   linkedin_password="myLinkedInPassword"
   llm_api_key="sk-abc123...your key..."
   ```
   👉 Put your key on the **llm_api_key** line, inside the " ".
4. Do not change any other line.
5. Save the file. Hold the **Ctrl** key and press **S**.

Your password and your key stay on your computer. They are never sent anywhere.

### Step 5 — Your resume
1. Have your resume ready as a **PDF** file.
2. On the left, open the **data** folder. Then open the **resumes** folder.
3. Put your PDF file inside the **resumes** folder. (You can pull it there with the
   mouse.)
4. Give the file this exact name: `Resume_Talita.pdf`
   - To change the name: click the file with the **right** mouse button, then click
     **Rename**, then type the name.
   - The name must be the same. Big **R** at the start.

---

## Part 3 — Start it (you do this every day) ▶️

Easy way: on the left, find the file **start.cmd**. Click it two times, fast.
A black window opens. The bot starts.

Then:
1. A **Chrome** window opens by itself.
2. **The first time only:** log in to your LinkedIn in that window. If it asks for a
   code from your phone, type it. After this first time, it stays logged in. It will
   not ask again.
3. Now wait. The bot looks for jobs and sends them for you. It **stops by itself
   after 30 jobs**.

### To stop it
- Close the Chrome window. Or click the black window and hold **Ctrl** and press **C**.
- To pause: hold **Ctrl** and press **X**. Wait a few seconds.

---

## What the bot does (so you are not surprised)

- ✅ It applies only to **junior marketing** jobs (entry and associate level).
- ⏭️ It **skips** jobs that ask you to write a long text (for example: "Why do you
  want this job?" or a cover letter). This is normal. The bot will not write these
  for you. So it passes some jobs. That is okay.
- 💰 For salary questions, it answers about **$65,000 to $75,000**. Or it uses the
  salary the job shows. There is no minimum.
- 🔁 You can start it again the next day to find new jobs.

---

## If there is a problem

1. Take a photo of the black window (show the last red words).
2. Send it to **Sam**.

You do not need to fix anything yourself. 🙂

---

<sub>Technical setup for engineers is in SETUP.md.</sub>
