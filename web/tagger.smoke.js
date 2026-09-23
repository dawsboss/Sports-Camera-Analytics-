/* What the tagger's gestures are supposed to do, checked in a real browser.
 *
 * The taps in this page are the dataset. A gesture that puts a mark a few
 * pixels from where someone meant it, or moves one they were only trying to
 * look at, writes a wrong label — and a wrong label is worse than a missing
 * one, because nothing downstream can tell. None of that is reachable from
 * pytest: it is pointer events, a CSS transform and a browser. So it is
 * checked here instead, and `pytest` stays what CLAUDE.md says it is.
 *
 * This is not part of CI. It needs Playwright and a Chromium:
 *
 *     npm i playwright
 *     node web/tagger.smoke.js
 *
 * The video never loads — these are file:// runs against a dead URL — which
 * is fine, because everything under test is where a tap lands, not what is
 * under it. A fixed height stands in for the frame.
 */
const { chromium } = require('playwright');
const nodePath = require('path');
const page_url = 'file://' + nodePath.join(__dirname, 'label.html');
const fails = [];
function ok(name, cond, extra) { console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')); if (!cond) fails.push(name); }

(async () => {
  // PW_CHROMIUM lets a preinstalled browser be used instead of Playwright's.
  const b = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  const page = await b.newPage({ viewport: { width: 400, height: 800 }, hasTouch: true, isMobile: true });
  page.on('pageerror', e => { console.log('PAGEERROR ' + e.message); fails.push('pageerror'); });
  await page.goto(page_url);
  await page.addStyleTag({ content: '#vid{height:225px !important;}' });

  // open a match (the video will not load; the tagging logic does not need it)
  await page.click('#linkCustom');
  await page.fill('.sheet input[type=text]', 'http://127.0.0.1:9/none.mp4');
  await page.click('.sheet .btn.primary');
  await page.waitForTimeout(150);
  ok('work screen open', await page.evaluate(() => document.body.classList.contains('working')));

  await page.addScriptTag({ content: `
    window.ptr = function (type, id, x, y) {
      var e = new PointerEvent(type, { pointerId: id, clientX: x, clientY: y, bubbles: true,
        cancelable: true, pointerType: 'touch', isPrimary: id === 1 });
      document.getElementById('stagewrap').dispatchEvent(e);
    };
    window.tap = function (x, y) { ptr('pointerdown', 1, x, y); ptr('pointerup', 1, x, y); };
    window.pinPos = function (sel) {
      var n = document.querySelector(sel); if (!n || n.hidden) return null;
      return [parseFloat(n.style.left), parseFloat(n.style.top)];
    };
    window.fx = function (u, v) {
      var r = document.getElementById('vid').getBoundingClientRect();
      return [r.left + r.width * u, r.top + r.height * v];
    };
    window.tapf = function (u, v) { var p = fx(u, v); tap(p[0], p[1]); };
    window.scale = function () {
      var t = getComputedStyle(document.getElementById('zoomer')).transform;
      var m = t.match(/matrix\\(([^,]+),/); return m ? parseFloat(m[1]) : 1;
    };
  ` });

  // --- ball: a tap places the pin and zooms in on it
  await page.evaluate(() => tapf(0.5, 0.5));
  await page.waitForTimeout(300);
  let st = await page.evaluate(() => ({ s: scale(), pin: pinPos('#marker'), lvl: document.getElementById('zoomLevel').textContent }));
  ok('tap places the ball pin', st.pin !== null, st.pin);
  ok('tap zooms to 4x', Math.abs(st.s - 4) < 0.01, st);

  // --- a pinch must not move the pin, and must change the zoom
  const before = st.pin;
  await page.evaluate(() => {
    var a = fx(0.35, 0.5), b = fx(0.65, 0.5), c = fx(0.15, 0.5), d = fx(0.85, 0.5);
    ptr('pointerdown', 1, a[0], a[1]); ptr('pointerdown', 2, b[0], b[1]);
    ptr('pointermove', 1, c[0], c[1]); ptr('pointermove', 2, d[0], d[1]);
    ptr('pointerup', 1, c[0], c[1]);   ptr('pointerup', 2, d[0], d[1]);
  });
  st = await page.evaluate(() => ({ s: scale(), pin: pinPos('#marker') }));
  ok('pinch leaves the pin where it was', JSON.stringify(st.pin) === JSON.stringify(before), { before, after: st.pin });
  ok('pinch scales by how far the fingers spread', Math.abs(st.s - 4 * (0.70 / 0.30)) < 0.2, st.s);

  // --- panning with one finger must not move the pin either
  await page.evaluate(() => {
    var a = fx(0.15, 0.2), b = fx(0.35, 0.4);
    ptr('pointerdown', 1, a[0], a[1]); ptr('pointermove', 1, b[0], b[1]); ptr('pointerup', 1, b[0], b[1]);
  });
  st = await page.evaluate(() => ({ pin: pinPos('#marker') }));
  ok('drag away from the pin pans, not places', JSON.stringify(st.pin) === JSON.stringify(before), { before, after: st.pin });

  // --- dragging the pin itself moves it
  const pc = await page.evaluate(() => {
    var n = document.getElementById('marker').getBoundingClientRect();
    return [n.left + n.width / 2, n.top + n.height / 2];
  });
  await page.evaluate(([x, y]) => { ptr('pointerdown', 1, x, y); ptr('pointermove', 1, x + 12, y); ptr('pointerup', 1, x + 12, y); }, pc);
  st = await page.evaluate(() => ({ pin: pinPos('#marker') }));
  ok('dragging the pin nudges it', st.pin[0] > before[0] + 0.1 && Math.abs(st.pin[1] - before[1]) < 0.2, { before, after: st.pin });

  // --- pitch mode: zoom control works, undo is a stack
  await page.click('#modePitch');
  await page.waitForTimeout(50);
  await page.click('#zoomIn');
  await page.waitForTimeout(300);
  st = await page.evaluate(() => scale());
  ok('pitch mode can zoom in', st > 1.5, st);
  await page.click('#zoomLevel');
  await page.waitForTimeout(300);
  ok('tapping the level fits the frame', (await page.evaluate(() => scale())) === 1);

  // The taps have to be where a camera would see those points: rings are
  // drawn only where the taps agree on a pitch, and taps along a diagonal
  // agree on none. A camera on the near touchline looking at the left box
  // of a 100 x 60 m field — smaller than the page's 120 x 70, as both real
  // fields are. The box's four corners fix everything inside it at any size.
  const VX = { 4: [0, 5050], 1: [0, 950], 9: [2015, 950], 12: [2015, 5050], 0: [0, 0], 5: [0, 6000],
               2: [0, 2084], 3: [0, 3916], 6: [550, 2084], 7: [550, 3916], 8: [1100, 3000],
               10: [2015, 2084], 11: [2015, 3916] };
  const seen = (i) => { const [X, Y] = VX[i], D = 7000 + (6000 - Y); return [0.5 + 2.5 * (X - 1000) / D, -0.3 + 9000 / D]; };
  const order = [4, 1, 9, 12, 0];
  for (const i of order) {
    await page.evaluate((i) => {
      var d = document.getElementById('dg' + i).getBoundingClientRect();
      var e = new PointerEvent('pointerdown', { clientX: d.left + d.width / 2, clientY: d.top + d.height / 2, bubbles: true, cancelable: true });
      document.getElementById('dg' + i).dispatchEvent(e);
    }, i);
    const armed = await page.evaluate(() => document.getElementById('dgName').textContent);
    await page.evaluate(([u, v]) => tapf(u, v), seen(i));
    if (i === order[0]) ok('arming a diagram dot names it', /placing/.test(armed), armed);
  }
  let pins = await page.evaluate(() => [...document.querySelectorAll('.pin.set')].map(n => Number(n.dataset.i)).sort((a, b) => a - b));
  ok('five points placed', pins.join(',') === '0,1,4,9,12', pins);
  ok('predictions appear after four', (await page.evaluate(() => document.querySelectorAll('.pin.pred').length)) > 0);
  // and each one is where the camera sees that point, not merely somewhere
  const rings = await page.evaluate(() => [...document.querySelectorAll('.pin.pred')]
    .map(n => [Number(n.dataset.i), parseFloat(n.style.left) / 100, parseFloat(n.style.top) / 100]));
  const off = rings.map(([i, u, v]) => VX[i] ? Math.hypot(u - seen(i)[0], (v - seen(i)[1]) * 9 / 16) : 1);
  ok('every ring sits on its point', rings.length > 0 && Math.max(...off) < 0.01, rings.map((r, k) => r[0] + ':' + off[k].toFixed(3)));

  // tapping a suggested ring takes that vertex
  const ring = await page.evaluate(() => {
    var armed = (document.getElementById('dgName').textContent.match(/placing (.+)$/) || [])[1] || '';
    var ns = [...document.querySelectorAll('.pin.pred')].filter(n => n.title.indexOf(armed) < 0);
    var r = ns[0].getBoundingClientRect();
    return { name: ns[0].title, x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  await page.evaluate(([x, y]) => tap(x, y), [ring.x, ring.y]);
  let hint = await page.evaluate(() => document.getElementById('hint').textContent);
  ok('tapping a ring accepts it and says which', /^took /.test(hint), hint);

  // a tap right beside a placed pin must not drag that pin
  const pin0Before = await page.evaluate(() => document.querySelector('.pin.set[data-i="0"]').style.left);
  const nearPin = await page.evaluate(() => {
    var n = document.querySelector('.pin.set[data-i="0"]').getBoundingClientRect();
    return [n.left + n.width / 2 + 12, n.top + n.height / 2 + 8];
  });
  const nBefore = await page.evaluate(() => document.querySelectorAll('.pin.set').length);
  await page.evaluate(([x, y]) => tap(x, y), nearPin);
  const pin0After = await page.evaluate(() => document.querySelector('.pin.set[data-i="0"]').style.left);
  const nAfter = await page.evaluate(() => document.querySelectorAll('.pin.set').length);
  ok('a tap beside a placed pin leaves it alone', pin0Before === pin0After, [pin0Before, pin0After]);
  ok('a tap beside a placed pin still places a point', nAfter === nBefore + 1, [nBefore, nAfter]);

  // dragging a placed pin moves that pin
  const grab = await page.evaluate(() => {
    var n = document.querySelector('.pin.set[data-i="0"]').getBoundingClientRect();
    return [n.left + n.width / 2, n.top + n.height / 2];
  });
  await page.evaluate(([x, y]) => { ptr('pointerdown', 1, x, y); ptr('pointermove', 1, x + 15, y + 9); ptr('pointerup', 1, x + 15, y + 9); }, grab);
  const pin0Dragged = await page.evaluate(() => document.querySelector('.pin.set[data-i="0"]').style.left);
  ok('dragging a placed pin moves it', parseFloat(pin0Dragged) > parseFloat(pin0Before), [pin0Before, pin0Dragged]);

  await page.click('#btnUndoPin');
  await page.click('#btnUndoPin');
  await page.click('#btnUndoPin');
  pins = await page.evaluate(() => [...document.querySelectorAll('.pin.set')].map(n => Number(n.dataset.i)).sort((a, b) => a - b));
  ok('undo walks back the placements in order, not by vertex number', pins.join(',') === '1,4,9,12', pins);
  ok('undo re-arms the point it removed', /placing/.test(await page.evaluate(() => document.getElementById('dgName').textContent)));

  // --- "can't see it" sets a point aside
  const dgArmed = await page.evaluate(() => document.getElementById('dgName').textContent);
  await page.click('#btnCantSee');
  const dgName = await page.evaluate(() => document.getElementById('dgName').textContent);
  ok("can't see it moves on", /placing/.test(dgName) && dgName !== dgArmed, [dgArmed, dgName]);
  ok('the set-aside dot is marked on the diagram', (await page.evaluate(() => document.querySelectorAll('.dg-dot.skip').length)) === 1);

  // Three points on the goal line and one off it fit a whole family of
  // homographies exactly. The old fit drew rings from an arbitrary member.
  // One tap is a finger's width out, as real ones are: exact taps made the
  // old solver give up, which hid the problem here.
  await page.click('#btnSkipFrame');
  await page.waitForTimeout(50);
  for (const i of [1, 2, 3, 9]) {
    await page.evaluate((i) => {
      var dot = document.getElementById('dg' + i), d = dot.getBoundingClientRect();
      if (!dot.classList.contains('armed')) dot.dispatchEvent(new PointerEvent('pointerdown',
        { clientX: d.left + d.width / 2, clientY: d.top + d.height / 2, bubbles: true, cancelable: true }));
    }, i);
    await page.evaluate(([u, v]) => tapf(u, v), [seen(i)[0] + (i === 2 ? 0.006 : 0), seen(i)[1]]);
  }
  pins = await page.evaluate(() => document.querySelectorAll('.pin.set').length);
  ok('three on a line and one off draw no rings', pins === 4 &&
    (await page.evaluate(() => document.querySelectorAll('.pin.pred').length)) === 0, pins);

  await b.close();
  console.log(fails.length ? '\nFAILED: ' + fails.join(', ') : '\nall good');
  process.exit(fails.length ? 1 : 0);
})();
