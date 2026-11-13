# 🎯 BPSC TRE Computer Science Telegram Quiz Bot (@Bpsc_tier4_bot)

Comprehensive Telegram Quiz Bot designed specifically for **BPSC TRE Computer Science (Part-III)** with 100% syllabus coverage, authentic 5-option BPSC format, native Quiz polls, and persistent progress tracking.

---

## 📊 Question Bank Overview (`quiz_bank/`)

| File | Subject | Questions | Answer Dist (`A/B/C/D/E`) |
| :--- | :--- | :---: | :--- |
| `00_pyq_combined.json` | TRE 1.0 + 2.0 + 3.0 CS PYQs | **240** | `A:69, B:74, C:61, D:20, E:16` |
| `00_pyq_mistakes_drill.json` | 75 Wrong PYQs (Flashcards) | **75** | `A:17, B:22, C:17, D:8, E:11` |
| `01_digital_electronics.json` | Digital Electronics & Number Systems | **100** | `A:31, B:31, C:30, D:7, E:1` |
| `02_coa_microprocessors.json` | COA & 8085/8086 Microprocessors | **100** | `A:26, B:26, C:25, D:11, E:12` |
| `03_data_structures.json` | Data Structures & Algorithms | **100** | `A:29, B:28, C:28, D:14, E:1` |
| `04_cpp_oops.json` | C++ & OOPs | **100** | `A:29, B:29, C:29, D:13, E:0` |
| `05_operating_systems.json` | Operating Systems & Systems Programming | **100** | `A:30, B:29, C:29, D:11, E:1` |
| `06_dbms_sql.json` | DBMS & SQL | **100** | `A:25, B:25, C:25, D:14, E:11` |
| `07_computer_networks.json` | Computer Networks & Security | **100** | `A:31, B:31, C:30, D:8, E:0` |
| `08_discrete_math.json` | Discrete Mathematics | **60** | `A:19, B:19, C:18, D:4, E:0` |
| `09_web_se_emerging.json` | Web Tech, SE & Emerging Tech | **60** | `A:13, B:13, C:13, D:11, E:10` |
| `10_toc_graphics_java_extra.json` | TOC, Computer Graphics, Java/JSP | **100** | `A:25, B:25, C:25, D:13, E:12` |
| `11_e_trap_special_drill.json` | D/E Trap Special Drill | **80** | `A:4, B:3, C:3, D:25, E:45` |
| `12_numericals_and_outputs.json` | Numericals & C++/SQL Outputs | **100** | `A:25, B:25, C:25, D:12, E:13` |
| `13_high_yield_facts_not_traps.json` | High-Yield Facts & NOT-Traps | **100** | `A:25, B:25, C:25, D:12, E:13` |
| **Total Questions** | **13 Modules + PYQs** | **1,440 Qs** | **Balanced 5-Option BPSC Format** |

---

## 🚀 Features

- **5-Option BPSC Format:** Strictly `(A)`, `(B)`, `(C)`, `(D) More than one of the above`, `(E) None of the above`.
- **Telegram Native Quiz Polls:** Instant right/wrong indicator and crisp explanation popup ($\le 200$ chars).
- **80-Q Exam Simulation:** Proportional weighted mock matching official BPSC CS question weightage.
- **Smart Mistake Tracker:** Wrong answers automatically get added to `My Mistakes` queue and get removed when answered correctly.
- **Timer Modes:** `Off (Study Mode)`, `30s`, `45s`, `60s` per question.
- **Detailed Stats:** `/stats` tracks subject-wise accuracy and target 75+/80 readiness.

---

## ☁️ 24/7 Hosting on Oracle Cloud Free Tier (Ubuntu)

### 1. Connect to your Oracle VPS:
```bash
ssh -i /path/to/your-key.key ubuntu@<YOUR_SERVER_PUBLIC_IP>
```

### 2. Clone and Setup:
```bash
# Update packages and install python3-pip
sudo apt update && sudo apt install -y python3-pip python3-venv git

# Clone repository
git clone https://github.com/hicker467-cell/bpsc.git
cd bpsc

# Setup Virtual Environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env with your bot token
echo "TELEGRAM_BOT_TOKEN=your_bot_token_here" > .env
```

### 3. Setup Systemd Service (24/7 Auto-Restart on Boot):
```bash
sudo cp bpsc_bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bpsc_bot
sudo systemctl start bpsc_bot
sudo systemctl status bpsc_bot
```

Done! Your bot is now running 24/7/365 without sleep on Oracle Cloud.
