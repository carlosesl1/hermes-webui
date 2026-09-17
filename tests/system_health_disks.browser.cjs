// Run with node; set PLAYWRIGHT_MODULE to an existing Playwright installation if needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const source = file => process.env.SYSTEM_HEALTH_BASELINE
  ? execFileSync('git', ['show', `HEAD:${file}`], {cwd: root, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024})
  : fs.readFileSync(path.join(root, file), 'utf8');
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const panels = source('static/panels.js');
    const ui = source('static/ui.js');
    await page.setContent('<html class="dark"><body><main style="width:100%;padding:16px;box-sizing:border-box"></main></body></html>');
    await page.addStyleTag({content: source('static/style.css')});
    await page.addScriptTag({content: panels.slice(panels.indexOf('function _renderSystemHealthPanel()'), panels.indexOf('function _renderLlmWikiStatus('))});
    await page.evaluate(() => document.querySelector('main').innerHTML = _renderSystemHealthPanel());
    await page.addScriptTag({content: "const $=id=>document.getElementById(id);\n" + ui.slice(ui.indexOf('function _systemHealthPercent('), ui.indexOf('async function pollSystemHealth('))});
    const disk = {device: '/dev/sda1', mountpoint: '/home/ubuntu', fs_type: 'ext4', used_bytes: 1024, total_bytes: 4096, free_bytes: 3072, percent: 25, available: true};
    const failed = {...disk, device: '/dev/sdb1', mountpoint: '/data', available: false, percent: null};
    const render = payload => page.evaluate(p => renderSystemHealth(p), payload);
    await render({available: true, cpu: {percent: 12}, memory: {percent: 50}, disks: [disk, disk, failed], disk: {...disk, percent: 99}});
    assert.equal(await page.locator('[data-system-health-disk]').count(), 2);
    assert.equal(await page.locator('.system-health-metric').count(), 4);
    assert.match(await page.locator('[data-system-health-disk]').first().innerText(), /sda1[\s\S]*25%[\s\S]*\/home\/ubuntu[\s\S]*1.0 KB \/ 4.0 KB · 3.0 KB free/);
    assert.match(await page.locator('[data-system-health-disk]').last().innerText(), /Unavailable/);
    assert.equal(await page.locator('[data-system-health-disk] .system-health-bar').last().getAttribute('aria-valuenow'), null);
    assert.equal(await page.locator('[data-system-health-metric="cpu"] [data-system-health-value]').innerText(), '12%');
    for (const width of [1440, 768, 375]) {
      await page.setViewportSize({width, height: 900});
      assert.equal(await page.evaluate(() => [...document.querySelectorAll('.system-health-metric')].every(el => el.scrollWidth <= el.clientWidth + 1)), true, `card overflow at ${width}`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `page overflow at ${width}`);
    }
    await render({disks: [{...disk, device: '/dev/<img onerror=alert(1)>', mountpoint: '/<script>bad</script>'}]});
    assert.equal(await page.locator('#systemHealthPanel img, #systemHealthPanel script').count(), 0);
    assert.match(await page.locator('.system-health-disk-mount').innerText(), /<script>/);
    for (const disks of [undefined, []]) {
      await render({disks, disk: {...disk, used_bytes: 0, percent: 0, free_bytes: 4096}});
      assert.equal(await page.locator('[data-system-health-disk]').count(), 1);
      assert.match(await page.locator('.system-health-capacity').innerText(), /0 B \/ 4.0 KB · 4.0 KB free/);
      assert.equal(await page.locator('[data-system-health-disk] [data-system-health-value]').innerText(), '0%');
    }
    await render({available: false});
    assert.equal(await page.locator('.system-health-disk-mount').innerText(), '');
    assert.equal(await page.locator('.system-health-capacity').innerText(), '');
    assert.equal(await page.locator('[data-system-health-metric="cpu"] [data-system-health-value]').innerText(), '—');
    await render({cpu: {percent: null}, disks: [disk]});
    assert.equal(await page.locator('[data-system-health-metric="cpu"] [data-system-health-value]').innerText(), '—');
    assert.equal(await page.locator('#systemHealthPanel').isVisible(), true);
    assert.deepEqual(errors, []);
    console.log('PASS: multi-disk, deduplication, unavailable/null, legacy/empty fallback, zero bytes, escaped data, recovery, ARIA and 1440/768/375px overflow checks');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
