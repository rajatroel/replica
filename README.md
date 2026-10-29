<div align="center">

# Replica

**Automated Instagram task handling for the SMM Kingdom Tasks Bot.**

![Platform](https://img.shields.io/badge/platform-Android%20%7C%20Termux-3DDC84?style=flat-square)
![Language](https://img.shields.io/badge/python-3.x-3776AB?style=flat-square)
![License](https://img.shields.io/badge/license-paid%20%2410%2Fmonth%2Fdevice-blue?style=flat-square)

</div>

---

## Overview

**Replica** is an automation tool that runs the **SMM Kingdom Tasks Bot** workflow for you. It receives Instagram tasks from the Telegram bot, performs them in the official Instagram app on your Android device, and reports the results back, with no manual effort.

---

## Table of Contents

- [Pre-requirements](#pre-requirements)
- [Setup Guides](#setup-guides)
  - [1. Set the Bot Language to English](#1-set-the-bot-language-to-english)
  - [2. Get Your Telegram API ID and API Hash](#2-get-your-telegram-api-id-and-api-hash)
  - [3. Get a Replica License Key](#3-get-a-replica-license-key)
  - [4. Prepare Your Instagram Accounts](#4-prepare-your-instagram-accounts)
  - [5. Second Phone for First-Time Login](#5-second-phone-for-first-time-login)
  - [6. Wi-Fi Requirement](#6-wi-fi-requirement)
- [Installation and Setup](#installation-and-setup)
- [Starting and Stopping the Automation](#starting-and-stopping-the-automation)
- [License and Pricing](#license-and-pricing)
- [FAQs](#faqs)
- [Support](#support)

---

## Pre-requirements

Make sure you have everything below before you begin.

| # | Requirement | Details |
|---|-------------|---------|
| 1 | **Termux** | [Download Termux](https://f-droid.org/packages/com.termux/) |
| 2 | **Termux:API** | [Download Termux:API](https://f-droid.org/packages/com.termux.api/) |
| 3 | **Replica** | [Download Replica](#) |
| 4 | **SMM Kingdom Tasks Bot language** | Must be set to **English** |
| 5 | **A second phone** | Official Telegram app, logged in to the account you want to automate (first-time login only) |
| 6 | **Official Instagram app** | All your VIP accounts for this Telegram bot logged in |
| 7 | **Replica license key** | Required to run the automation |
| 8 | **Telegram API ID and API Hash** | From Telegram's developer portal |
| 9 | **Wi-Fi connection** | Needed for the first-time setup only |

> **Note:** Install Termux and Termux:API from the same source (for example, both from F-Droid) so they stay compatible.

---

## Setup Guides

### 1. Set the Bot Language to English

Replica reads the bot's messages in English, so the language must be set before you start.

1. Open Telegram and go to the **SMM Kingdom Tasks Bot**.
2. Send `/start` if you have not already.
3. Open the bot's **Settings** or **Language** menu.
4. Choose **English**.
5. Confirm that the bot's replies now appear in English.

### 2. Get Your Telegram API ID and API Hash

1. Open [my.telegram.org](https://my.telegram.org) in a browser.
2. Enter the phone number of the Telegram account you want to automate (with country code) and tap **Next**.
3. Enter the login code that Telegram sends to your app.
4. Click **API development tools**.
5. Fill in the form:
   - **App title:** any name, for example `Replica`
   - **Short name:** any short name
   - **Platform:** Android or Other
6. Click **Create application**.
7. Copy your **App api_id** and **App api_hash**.

> **Important:** Keep your API ID and API Hash private. Never share them publicly.

### 3. Get a Replica License Key

1. Contact the admin on Telegram: **[@iamrajatroel](https://t.me/iamrajatroel)**.
2. Pay for your license ($10 per month per device, see [License and Pricing](#license-and-pricing)).
3. The admin will send you your license key.
4. Keep the key safe. You will enter it in the Replica app during setup.

> The license is tied to your device. Each additional device needs its own license.

### 4. Prepare Your Instagram Accounts

- Install the **official Instagram app** on the device that will run Replica.
- Log in to **all VIP accounts** that you have registered with the SMM Kingdom Tasks Bot.
- Make sure every account stays logged in and works normally.

### 5. Second Phone for First-Time Login

Replica signs in to Telegram on first run by showing a QR code that you scan from another device.

1. Take a **second phone** with the **official Telegram app** installed.
2. Log in to the **same Telegram account** you want to automate.
3. When Replica displays the login QR code, scan it from the second phone:
   **Telegram, then Settings, then Devices, then Link Desktop Device**.
4. If the account has two-step verification, enter your 2FA password when asked.

This is only required for the first login. Later runs reuse the saved session.

### 6. Wi-Fi Requirement

A **Wi-Fi connection is needed only for the first-time setup**. It is used for the one-time ADB wireless pairing and the initial login. After setup, the automation does not require Wi-Fi.

---

## Installation and Setup

Follow the tutorial video to complete the setup step by step:

**[Click here to view the setup tutorial video](#)**

---

## Starting and Stopping the Automation

Once setup is complete:

**Start**

1. Open **Termux**.
2. Run Replica:
   ```bash
   python replica.py
   ```
3. Wait for the status messages to confirm the license, ADB connection and Telegram connection.
4. Tap **START AUTOMATION**.

**Stop**

- Tap **STOP AUTOMATION** (the same button, which changes while running).
- Tap **EXIT** to close Replica and Termux completely. Exit is available only while the automation is stopped.

Keyboard shortcuts (if you use a keyboard): `S` to start and `X` to stop.

**Logs**

- All activity is saved to `logs.txt` in the Termux home folder.
- Use **SHARE** to send the log file, or **CLEAR** to empty it.

---

## License and Pricing

| Plan | Price |
|------|-------|
| Replica License | **$10 per month, per device** |

To purchase or renew, contact **[@iamrajatroel](https://t.me/iamrajatroel)**.

---

## FAQs

**What does Replica do?**
It automates the Instagram tasks given by the SMM Kingdom Tasks Bot, so you do not have to complete them manually.

**Do I need Wi-Fi all the time?**
No. Wi-Fi is needed only for the first-time setup.

**Why do I need a second phone?**
Telegram's first login is done by scanning a QR code from another device that is already logged in to the account.

**Can I use one license on multiple devices?**
No. The license is $10 per month per device.

**The bot is not responding correctly. What should I check?**
Confirm the SMM Kingdom Tasks Bot language is set to **English** and that all VIP Instagram accounts are logged in.

**Replica says the license is invalid or expired.**
Check that the key was entered correctly in the Replica app. If it has expired, contact the admin to renew it.

**Replica says "Create config first using Replica app".**
Open the Replica app, enter your API ID, API Hash, license key and accounts, tap **Save**, then run the script again.

**The Telegram login QR expired.**
Wait about two minutes and try again, then scan the new QR code promptly.

**How do I fully reset the Telegram login?**
Remove the active session from Telegram (**Settings, Devices**) and set up again with the second phone.

---

## Support

For license purchases, renewals, or help with setup, contact the admin on Telegram:

**[@iamrajatroel](https://t.me/iamrajatroel)**

---

<div align="center">

Replica is an independent tool and is not affiliated with Telegram, Instagram or SMM Kingdom.

</div>
