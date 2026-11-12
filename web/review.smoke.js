// Drives the review page in a real Chromium against a running Sideline API
// and asserts what the taps do: the list renders, the frame draws, Right
// writes a row and advances, Wrong taker takes the tapped box. Not part of
// pytest (tests/test_review_api.py covers the routes); run it by hand:
//
//   sideline serve --data <dir with a reviewed match> --port 8765 &
//   node web/review.smoke.js http://localhost:8765 <match id> [a clip to add through the form]
const { chromium } = require('playwright');

(async () => {
  const [base, matchId] = process.argv.slice(2);
  if (!base || !matchId) { console.error('usage: node review.smoke.js <api base> <match id>'); process.exit(2); }
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  // Without a match the page lists the matches this API knows, and takes film.
  await page.goto(`${base}/review`);
  await page.waitForSelector('a.ev');
  const links = await page.$$eval('a.ev', els => els.map(e => e.getAttribute('href')));
  if (!links.some(h => h.includes(matchId))) throw new Error(`match list lacks ${matchId}: ${links}`);
  console.log('match list:', links.length, 'link(s)');
  const clipPath = process.argv[4];
  if (clipPath) {
    await page.setInputFiles('#f_file', clipPath);
    await page.fill('#f_kick', '0:01');
    await page.click('#f_go');
    // The upload returns at once; the list then shows the stages running and finally sampled.
    await page.waitForFunction(() => document.querySelectorAll('a.ev').length >= 2, null, { timeout: 15000 });
    await page.waitForFunction(() => [...document.querySelectorAll('.status')].some(s => /needs detection|ready to review/.test(s.textContent)), null, { timeout: 60000 });
    const statuses = await page.$$eval('.status', els => els.map(e => e.textContent));
    console.log('after adding film:', JSON.stringify(statuses));
  }
  await page.goto(`${base}/review?match=${matchId}`);
  await page.waitForSelector('.ev');
  const rows = await page.$$eval('.ev', els => els.map(e => e.textContent));
  console.log('events listed:', rows.length, JSON.stringify(rows[0]));
  await page.waitForFunction(() => document.querySelector('#caption') && !document.querySelector('#caption').hidden);
  // The frame must have drawn something that is not black.
  await page.waitForFunction(() => {
    const c = document.getElementById('c'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let bright = 0; for (let i = 0; i < d.length; i += 40) if (d[i] + d[i + 1] + d[i + 2] > 120) bright++;
    return bright > 50;
  }, null, { timeout: 5000 });
  const caption = await page.$eval('#caption', e => e.textContent);
  console.log('caption:', caption.trim().replace(/\s+/g, ' '));
  // The kind chips: one per kind present plus "all", counts that add up,
  // and a tap narrows the list to that kind and records it in the address.
  const chips = await page.$$eval('#kinds .chip', els => els.map(e => ({ kind: e.dataset.kind, n: Number(e.querySelector('small').textContent) })));
  if (chips.length) {
    const total = chips.find(c => !c.kind).n, sum = chips.filter(c => c.kind).reduce((a, c) => a + c.n, 0);
    if (total !== rows.length || sum !== total) throw new Error(`chip counts ${JSON.stringify(chips)} against ${rows.length} rows`);
    const pick = chips.filter(c => c.kind).sort((a, b) => a.n - b.n)[0];
    await page.click(`#kinds .chip[data-kind="${pick.kind}"]`);
    await page.waitForFunction(n => document.querySelectorAll('#events .ev').length === n, pick.n);
    const kinds = await page.$$eval('#events .ev .kind', els => [...new Set(els.map(e => e.textContent))]);
    if (kinds.length !== 1 || kinds[0] !== pick.kind.replace('_', ' ')) throw new Error(`filtered to ${pick.kind} but the list shows ${kinds}`);
    if (new URL(page.url()).searchParams.get('kind') !== pick.kind) throw new Error('the filter is not in the address');
    if (!(await page.$('#events .ev.sel'))) throw new Error('nothing selected after filtering');
    await page.click('#kinds .chip[data-kind=""]');
    await page.waitForFunction(n => document.querySelectorAll('#events .ev').length === n, rows.length);
    console.log('kind chips:', JSON.stringify(chips), 'filtered to', pick.kind, 'and back');
  }
  // Step and play.
  await page.keyboard.press('ArrowRight');
  await page.click('#play');
  await page.waitForTimeout(500);
  const t = await page.$eval('#time', e => e.textContent);
  console.log('time after play:', t.trim());
  // Wrong taker: tap the first non-taker person box.
  await page.click('#wrongtaker');
  const hint = await page.$eval('#hint', e => !e.hidden);
  if (!hint) throw new Error('wrong-taker hint did not show');
  const box = await page.evaluate(() => {
    const c = document.getElementById('c'); const r = c.getBoundingClientRect();
    // Reach into the page's frame data through the canvas size: pick a drawn box by probing the DOM state is
    // not exposed, so click where the scripted scene puts a standing player (x=120, foot y=140, 20 tall).
    return { x: r.left + 120 * r.width / c.width, y: r.top + 130 * r.height / c.height };
  });
  await page.mouse.click(box.x, box.y);
  await page.waitForFunction(() => document.querySelector('.badge.wrong_taker'));
  console.log('wrong taker recorded');
  // Right on the (now re-selected or same) event via the key.
  await page.keyboard.press('1');
  await page.waitForFunction(() => document.querySelector('.badge.right'));
  console.log('right recorded');
  const count = await page.$eval('#count', e => e.textContent);
  console.log('count:', count);
  const decisions = await (await fetch(`${base}/matches/${matchId}/review/decisions`)).json();
  console.log('rows on the API:', decisions.map(d => d.verdict).join(', '));
  if (errors.length) { console.error('page errors:', errors); process.exit(1); }
  if (decisions.length < 2) { console.error('expected two decisions'); process.exit(1); }
  await browser.close();
  console.log('review page smoke: ok');
})().catch(e => { console.error(e); process.exit(1); });
