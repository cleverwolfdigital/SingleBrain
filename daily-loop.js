import cron from 'node-cron';
import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const LOG_FILE    = path.join(ROOT, 'daily-loop.log');
const STATE_FILE  = path.join(ROOT, '.daily-loop-state.json');
const RAW_DIR     = path.join(ROOT, 'knowledge', 'raw');
const INSIGHTS    = path.join(ROOT, 'knowledge', 'INSIGHTS.md');

const DISCORD_WEBHOOK = process.env.DISCORD_WEBHOOK_URL;
const OPENROUTER_KEY  = process.env.OPENROUTER_API_KEY;

// ── Logging ───────────────────────────────────────────────────────────────────
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  fs.appendFileSync(LOG_FILE, line);
  process.stdout.write(line);
}

// ── State (tracks last run timestamp to find new raw files) ───────────────────
function getLastRun() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')).lastRun ?? 0; }
  catch { return 0; }
}
function setLastRun() {
  fs.writeFileSync(STATE_FILE, JSON.stringify({ lastRun: Date.now() }));
}

// ── Discord ───────────────────────────────────────────────────────────────────
async function discord(message) {
  if (!DISCORD_WEBHOOK) { log('DISCORD_WEBHOOK_URL not set — skipping post.'); return; }
  const res = await fetch(DISCORD_WEBHOOK, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: message }),
  });
  if (!res.ok) throw new Error(`Discord webhook ${res.status}: ${await res.text()}`);
}

// ── OpenRouter distillation ───────────────────────────────────────────────────
async function distill(filePath) {
  if (!OPENROUTER_KEY) throw new Error('OPENROUTER_API_KEY not set');
  const filename = path.basename(filePath);
  const content  = fs.readFileSync(filePath, 'utf8');
  const today    = new Date().toLocaleDateString('en-US', { timeZone: 'Pacific/Honolulu' });

  const res = await fetch('https://openrouter.ai/api/v1/chat/completions', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${OPENROUTER_KEY}`,
      'Content-Type': 'application/json',
      'HTTP-Referer': 'https://github.com/cleverwolfdigital/SingleBrain',
    },
    body: JSON.stringify({
      model: 'anthropic/claude-haiku-4-5',
      messages: [{
        role: 'user',
        content: `You are extracting marketing intelligence for SingleBrain, a context layer for Clever Wolf Digital.

File: ${filename}
Date: ${today}

Raw content:
---
${content}
---

Reply in this exact format — no extra commentary:

| ${filename} | ${today} | [1–3 reusable principles, pipe-separated] | [concrete action to take] | [PLAYBOOK / HOOKS / BRAND_VOICE / none] |`,
      }],
    }),
  });

  if (!res.ok) throw new Error(`OpenRouter ${res.status}: ${await res.text()}`);
  const data = await res.json();
  return data.choices[0].message.content.trim();
}

// ── Git helpers ───────────────────────────────────────────────────────────────
function git(cmd) {
  return execSync(`git ${cmd}`, { cwd: ROOT, stdio: 'pipe' }).toString().trim();
}
function hasChanges() {
  return git('status --porcelain').length > 0;
}

// ── MORNING: 8:00 AM HST ─────────────────────────────────────────────────────
async function morning() {
  log('=== MORNING START ===');

  // 1. git pull
  try { log(git('pull origin main')); }
  catch (e) { log(`git pull failed: ${e.message}`); }

  // 2. Scan /knowledge/raw/ for files new since last run
  const lastRun = getLastRun();
  let rows = [];
  try {
    const files = fs.readdirSync(RAW_DIR).filter(f => f !== '.gitkeep');
    const newFiles = files.filter(f => {
      const mtime = fs.statSync(path.join(RAW_DIR, f)).mtimeMs;
      return mtime > lastRun;
    });

    if (newFiles.length) {
      log(`New raw files: ${newFiles.join(', ')}`);
      for (const f of newFiles) {
        try {
          const row = await distill(path.join(RAW_DIR, f));
          rows.push(row);
          log(`Distilled: ${f}`);
        } catch (e) { log(`Distill failed (${f}): ${e.message}`); }
      }
      if (rows.length) {
        fs.appendFileSync(INSIGHTS, '\n' + rows.join('\n') + '\n');
        log(`Appended ${rows.length} row(s) to INSIGHTS.md`);
      }
    } else {
      log('No new raw files.');
    }
  } catch (e) { log(`Raw scan error: ${e.message}`); }

  setLastRun();

  // 3. Discord morning nudge
  try {
    await discord('Morning. Log yesterday\'s sprint numbers: sent / replies / calls booked / closes / revenue collected. Then pick the one action that gets a message sent or a call booked today.');
    log('Discord morning sent.');
  } catch (e) { log(`Discord failed: ${e.message}`); }

  // 4. Commit + push if changes
  try {
    if (hasChanges()) {
      git('add -A');
      git('commit -m "daily: sync + distill raw notes"');
      git('push origin main');
      log('Changes pushed.');
    } else {
      log('Nothing to commit.');
    }
  } catch (e) { log(`git push failed: ${e.message}`); }

  log('=== MORNING END ===');
}

// ── EVENING: 6:00 PM HST ─────────────────────────────────────────────────────
async function evening() {
  log('=== EVENING START ===');
  try {
    await discord("End of day. Did this week's ONE marketing action move? Update PLAYBOOK if yes.");
    log('Discord evening sent.');
  } catch (e) { log(`Discord failed: ${e.message}`); }
  log('=== EVENING END ===');
}

// ── Schedule ──────────────────────────────────────────────────────────────────
cron.schedule('0 8 * * *', morning, { timezone: 'Pacific/Honolulu' });
cron.schedule('0 18 * * *', evening, { timezone: 'Pacific/Honolulu' });

log('daily-loop.js running. Next: 8:00 AM + 6:00 PM HST daily.');
