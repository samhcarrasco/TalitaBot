# Talita's Job Bot 💼

> 🇧🇷 **Versão em português (Brasil): [role para baixo](#-guia-em-português-brasil-).**

This program uses your LinkedIn. It looks for marketing jobs. It fills the
"Easy Apply" forms for you. It sends them for you.

This page shows you how to start it. Follow the steps in order, from top to bottom.

Do not worry. You do not need to know about computers. If you see red words (an
error) that you do not understand, take a photo of the screen and send it to Sam.

---

## Part 1 — Set up (you do this ONE time)

### Step 1 — Copy the bot to your computer
1. Open the program **VS Code**.
2. Hold **Ctrl** and press the **`** key. (That key is at the top-left of the
   keyboard, under **Esc**. It has the marks **`** and **~** on it.) A box opens at
   the bottom. This box is where you type. It is called the *terminal*.
   - (If the box does not open: at the top, click **Terminal**, then **New Terminal**.)
3. Copy the line below. Click inside the box, paste it, and press **Enter**:
   ```
   git clone https://github.com/samhcarrasco/TalitaBot.git
   ```
4. Wait until it stops and you get a new empty line. The bot is now on your computer,
   in a folder named **TalitaBot**.
5. Now open that folder. Copy the line below, paste it in the same box, press **Enter**:
   ```
   code -r TalitaBot
   ```
   VS Code opens the bot. On the left side, you now see a list of files.
   - (If that did not work: click **File**, then **Open Folder**, find **TalitaBot**,
     and click **Select Folder**.)

### Step 2 — Your LinkedIn login and your key
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

### Step 3 — Your resume
1. Have your resume ready as a **PDF** file.
2. On the left, open the **data** folder. Then open the **resumes** folder.
3. Put your PDF file inside the **resumes** folder. (You can pull it there with the
   mouse.)
4. Give the file this exact name: `Resume_Talita.pdf`
   - To change the name: click the file with the **right** mouse button, then click
     **Rename**, then type the name.
   - The name must be the same. Big **R** at the start.

---

## Part 2 — Start it (you do this every day) ▶️

On the left, find the file **start.cmd**. Click it two times, fast.
A black window opens. The bot starts.

> ⏳ **The very first time**, the black window can take a few minutes before anything
> happens. This is normal. Just wait.

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

### To open the bot again another day
- Open **VS Code**. Click **File**, then **Open Folder**, then choose **TalitaBot**.
- (You do Step 1 only one time. You do not clone it again.)

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
---

# 🇧🇷 Guia em Português (Brasil)

Este programa usa o seu LinkedIn. Ele procura vagas de marketing. Ele preenche os
formulários de "Easy Apply" (Candidatura Simplificada) para você. E envia as
candidaturas para você.

Esta página mostra como começar. Siga os passos na ordem, de cima para baixo.

Não se preocupe. Você não precisa entender de computador. Se aparecerem palavras em
vermelho (um erro) que você não entende, tire uma foto da tela e mande para o Sam.

---

## Parte 1 — Preparar (você faz isso UMA vez)

### Passo 1 — Copiar o robô para o seu computador
1. Abra o programa **VS Code**.
2. Segure **Ctrl** e aperte a tecla **`**. (Essa tecla fica no canto superior esquerdo
   do teclado, embaixo do **Esc**. Ela tem os sinais **`** e **~**.) Uma caixa abre
   embaixo. É nela que você digita. Chama-se *terminal*.
   - (Se a caixa não abrir: lá em cima, clique em **Terminal**, depois **New Terminal**.)
3. Copie a linha abaixo. Clique dentro da caixa, cole e aperte **Enter**:
   ```
   git clone https://github.com/samhcarrasco/TalitaBot.git
   ```
4. Espere até parar e aparecer uma linha nova vazia. O robô já está no seu computador,
   numa pasta chamada **TalitaBot**.
5. Agora abra essa pasta. Copie a linha abaixo, cole na mesma caixa e aperte **Enter**:
   ```
   code -r TalitaBot
   ```
   O VS Code abre o robô. Do lado esquerdo, aparece a lista de arquivos.
   - (Se não funcionar: clique em **File**, depois **Open Folder**, ache **TalitaBot**
     e clique em **Select Folder**.)

### Passo 2 — Seu login do LinkedIn e sua chave
1. Do lado esquerdo, clique no arquivo chamado **.env**. Ele abre.
2. Cada linha tem este formato: `nome="valor"`. Você só muda as palavras **dentro das
   " "**. Não mude mais nada.
3. Mude estas três linhas. Quando você abre, elas estão assim:
   ```
   linkedin_email="your_linkedin_email@example.com"
   linkedin_password="your_linkedin_password"
   llm_api_key="your_llm_api_key"
   ```
   Coloque as suas informações dentro das " ". Assim:
   ```
   linkedin_email="talita@gmail.com"
   linkedin_password="minhaSenhaDoLinkedin"
   llm_api_key="sk-abc123...sua chave..."
   ```
   👉 Coloque a sua chave na linha **llm_api_key**, dentro das " ".
4. Não mude nenhuma outra linha.
5. Salve o arquivo. Segure **Ctrl** e aperte **S**.

Sua senha e sua chave ficam só no seu computador. Elas nunca são enviadas para lugar
nenhum.

### Passo 3 — Seu currículo
1. Tenha o seu currículo pronto em **PDF**.
2. Do lado esquerdo, abra a pasta **data**. Depois abra a pasta **resumes**.
3. Coloque o seu arquivo PDF dentro da pasta **resumes**. (Você pode arrastar com o
   mouse.)
4. Dê ao arquivo exatamente este nome: `Resume_Talita.pdf`
   - Para mudar o nome: clique no arquivo com o botão **direito** do mouse, clique em
     **Rename** (Renomear) e digite o nome.
   - O nome tem que ser igualzinho. **R** maiúsculo no começo.

---

## Parte 2 — Ligar o robô (você faz isso todo dia) ▶️

Do lado esquerdo, procure o arquivo **start.cmd**. Clique nele duas vezes, rápido.
Uma janela preta abre. O robô começa.

> ⏳ **Na primeira vez**, a janela preta pode demorar alguns minutos antes de acontecer
> algo. Isso é normal. É só esperar.

Depois:
1. Uma janela do **Chrome** abre sozinha.
2. **Só na primeira vez:** faça login no seu LinkedIn nessa janela. Se pedir um código
   do seu celular, digite. Depois dessa primeira vez, ele continua logado. Não vai
   pedir de novo.
3. Agora espere. O robô procura vagas e envia para você. Ele **para sozinho depois de
   30 vagas**.

### Para parar
- Feche a janela do Chrome. Ou clique na janela preta e segure **Ctrl** e aperte **C**.
- Para pausar: segure **Ctrl** e aperte **X**. Espere alguns segundos.

### Para abrir o robô outro dia
- Abra o **VS Code**. Clique em **File**, depois **Open Folder**, e escolha **TalitaBot**.
- (Você faz o Passo 1 só uma vez. Não precisa clonar de novo.)

---

## O que o robô faz (para você não se assustar)

- ✅ Ele se candidata só a vagas de **marketing júnior** (nível entry e associate).
- ⏭️ Ele **pula** vagas que pedem para escrever um texto longo (por exemplo: "Por que
  você quer esta vaga?" ou uma carta de apresentação). Isso é normal. O robô não
  escreve esses textos por você. Então ele deixa passar algumas vagas. Tudo bem.
- 💰 Nas perguntas de salário, ele responde em torno de **US$ 65.000 a US$ 75.000**. Ou
  usa o salário que a vaga mostra. Não tem valor mínimo.
- 🔁 Você pode ligar de novo no dia seguinte para achar vagas novas.

---

## Se der algum problema

1. Tire uma foto da janela preta (mostrando as últimas palavras em vermelho).
2. Mande para o **Sam**.

Você não precisa consertar nada sozinha. 🙂

---

<sub>Technical setup for engineers is in SETUP.md.</sub>
