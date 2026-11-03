(() => {
  'use strict';

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const header = document.querySelector('.site-header');
  const updateHeader = () => header?.classList.toggle('is-scrolled', window.scrollY > 72);
  updateHeader();
  window.addEventListener('scroll', updateHeader, { passive: true });

  // Findings count up from zero as they are revealed. The markup holds the final values, so a
  // render that never reveals them (no script, reduced motion, a static capture) still reads correctly.
  const formatCount = (node, value) => {
    const decimals = Number(node.dataset.decimals || 0);
    const number = decimals
      ? value.toFixed(decimals)
      : Math.round(value).toLocaleString('en-US');
    return `${node.dataset.prefix || ''}${number}${node.dataset.suffix || ''}`;
  };

  const countUp = group => {
    const nodes = [...group.querySelectorAll('[data-count-to]')];
    const started = performance.now();
    const duration = 1400;
    const step = time => {
      const progress = Math.max(0, Math.min(1, (time - started) / duration));
      const eased = 1 - Math.pow(1 - progress, 3);
      nodes.forEach(node => {
        const from = Number(node.dataset.countFrom || 0);
        const to = Number(node.dataset.countTo);
        node.textContent = formatCount(node, from + (to - from) * eased);
      });
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };

  const revealItems = [...document.querySelectorAll('[data-reveal]')];
  if (reducedMotion.matches || !('IntersectionObserver' in window)) {
    revealItems.forEach(item => item.classList.add('revealed'));
  } else {
    const revealObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('revealed');
        revealObserver.unobserve(entry.target);
        if ('countGroup' in entry.target.dataset) countUp(entry.target);
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -4% 0px' });
    revealItems.forEach(item => revealObserver.observe(item));
  }

  window.TokenMeterTabs.setupAll(document);

  const copyButton = document.getElementById('copy-install');
  const copyStatus = document.getElementById('copy-status');
  let copyTimer = 0;

  const writeClipboard = async text => {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const input = document.createElement('textarea');
    input.value = text;
    input.setAttribute('readonly', '');
    input.style.position = 'fixed';
    input.style.opacity = '0';
    document.body.appendChild(input);
    input.select();
    const copied = document.execCommand('copy');
    input.remove();
    if (!copied) throw new Error('Copy unavailable');
  };

  copyButton?.addEventListener('click', async () => {
    const activePanel = document.querySelector('.command-panel:not([hidden]) code');
    if (!activePanel) return;
    try {
      await writeClipboard(activePanel.textContent.trim());
      copyButton.querySelector('span').textContent = 'Copied';
      copyStatus.textContent = 'Command copied';
    } catch {
      copyButton.querySelector('span').textContent = 'Select';
      copyStatus.textContent = 'Select the command to copy it';
      const range = document.createRange();
      range.selectNodeContents(activePanel);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
    window.clearTimeout(copyTimer);
    copyTimer = window.setTimeout(() => {
      copyButton.querySelector('span').textContent = 'Copy';
      copyStatus.textContent = '';
    }, 2200);
  });

  const bayer = [
    0, 8, 2, 10,
    12, 4, 14, 6,
    3, 11, 1, 9,
    15, 7, 13, 5,
  ];

  const clamp = value => Math.max(0, Math.min(1, value));
  const gaussian = (value, center, spread) => Math.exp(-Math.pow(value - center, 2) / spread);

  class DitherField {
    constructor(canvas) {
      this.canvas = canvas;
      this.context = canvas.getContext('2d', { alpha: true });
      this.host = canvas.parentElement;
      this.mode = canvas.dataset.dither;
      this.cell = this.mode === 'plume' ? 7 : 8;
      this.pointer = { x: 0.66, y: 0.44 };
      this.visible = true;
      this.frame = 0;
      this.lastFrame = -100;
      this.width = 0;
      this.height = 0;

      this.onPointer = event => {
        const bounds = this.canvas.getBoundingClientRect();
        this.pointer.x = clamp((event.clientX - bounds.left) / Math.max(bounds.width, 1));
        this.pointer.y = clamp((event.clientY - bounds.top) / Math.max(bounds.height, 1));
      };
      this.onResize = () => {
        this.resize();
        this.draw(0);
      };

      this.host.addEventListener('pointermove', this.onPointer, { passive: true });
      window.addEventListener('resize', this.onResize, { passive: true });
      this.resize();

      if ('IntersectionObserver' in window) {
        this.observer = new IntersectionObserver(entries => {
          this.visible = entries[0]?.isIntersecting ?? true;
          if (this.visible) this.start();
          else this.stop();
        }, { rootMargin: '160px' });
        this.observer.observe(canvas);
      }

      this.draw(0);
      this.start();
    }

    resize() {
      const width = Math.max(1, Math.round(this.canvas.clientWidth));
      const height = Math.max(1, Math.round(this.canvas.clientHeight));
      if (width === this.width && height === this.height) return;
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      this.canvas.width = Math.round(width * ratio);
      this.canvas.height = Math.round(height * ratio);
      this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
      this.width = width;
      this.height = height;
    }

    samplePlume(nx, ny, phase) {
      const sweep = 0.54 + Math.sin(nx * 5.8 + phase) * 0.16;
      const ridge = gaussian(ny, sweep, 0.022 + nx * 0.06);
      const shoulder = gaussian(ny, 0.2 + nx * 0.45, 0.08);
      const pointer = Math.exp(-Math.hypot(nx - this.pointer.x, ny - this.pointer.y) * 5.4);
      const cut = gaussian(nx, 0.11 + ny * 0.34, 0.012);
      return clamp(ridge * 0.92 + shoulder * 0.38 + pointer * 0.34 - cut * 0.48 - 0.05);
    }

    sampleScan(nx, ny, phase) {
      const diagonal = gaussian(ny, 0.76 - nx * 0.52 + Math.sin(phase + nx * 9) * 0.035, 0.009);
      const band = gaussian(ny, 0.47 + Math.sin(nx * 11 - phase * 0.7) * 0.07, 0.045);
      const pulse = Math.exp(-Math.hypot(nx - this.pointer.x, (ny - this.pointer.y) * 1.7) * 6);
      const comb = (Math.sin(nx * 54 + phase * 3) + 1) * 0.08;
      return clamp(diagonal * 0.95 + band * (0.34 + comb) + pulse * 0.3 - 0.07);
    }

    sampleOrbit(nx, ny, phase) {
      const x = (nx - 0.53) * 1.14;
      const y = ny - 0.51;
      const radius = Math.hypot(x, y);
      const closedRing = gaussian(radius, 0.29 + Math.sin(phase) * 0.012, 0.0026);
      const boundaryEcho = gaussian(radius, 0.225 + Math.sin(phase * 0.7) * 0.006, 0.0018) * 0.34;
      const pointer = Math.exp(-Math.hypot(nx - this.pointer.x, ny - this.pointer.y) * 7);
      return clamp(closedRing * 1.08 + boundaryEcho + pointer * 0.18);
    }

    signalAt(nx, ny, phase) {
      switch (this.mode) {
        case 'plume': return this.samplePlume(nx, ny, phase);
        case 'scan': return this.sampleScan(nx, ny, phase);
        case 'orbit': return this.sampleOrbit(nx, ny, phase);
        default: return 0;
      }
    }

    colorAt(signal, nx, ny) {
      switch (this.mode) {
        case 'plume':
          return signal > 0.72 || nx + ny > 1.34 ? '#c7ff38' : '#31d9ff';
        case 'scan':
          return signal > 0.76 ? '#c7ff38' : '#31d9ff';
        case 'orbit':
          return signal > 0.7 ? '#00a9d2' : '#0a0b09';
        default:
          return '#31d9ff';
      }
    }

    draw(time) {
      const { context, width, height, cell } = this;
      if (!width || !height) return;
      context.clearRect(0, 0, width, height);
      const phase = time * 0.00032;

      for (let row = 0, y = 0; y < height; row += 1, y += cell) {
        for (let column = 0, x = 0; x < width; column += 1, x += cell) {
          const nx = x / width;
          const ny = y / height;
          const signal = this.signalAt(nx, ny, phase);
          const threshold = (bayer[(row % 4) * 4 + (column % 4)] + 0.5) / 16;
          if (signal < threshold) continue;
          context.fillStyle = this.colorAt(signal, nx, ny);
          const size = signal > 0.78 ? cell * 0.8 : signal > 0.48 ? cell * 0.58 : cell * 0.34;
          context.fillRect(x, y, size, size);
        }
      }
    }

    start() {
      if (this.frame || !this.visible || document.hidden || reducedMotion.matches) return;
      const loop = time => {
        this.frame = 0;
        if (!this.visible || document.hidden || reducedMotion.matches) return;
        if (time - this.lastFrame > 42) {
          this.draw(time);
          this.lastFrame = time;
        }
        this.frame = requestAnimationFrame(loop);
      };
      this.frame = requestAnimationFrame(loop);
    }

    stop() {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
    }
  }

  const ditherFields = [...document.querySelectorAll('.dither-canvas')]
    .map(canvas => new DitherField(canvas));

  document.addEventListener('visibilitychange', () => {
    ditherFields.forEach(field => document.hidden ? field.stop() : field.start());
  });

  const handleMotionChange = event => {
    ditherFields.forEach(field => {
      if (event.matches) {
        field.stop();
        field.draw(0);
      } else {
        field.start();
      }
    });
  };

  if (reducedMotion.addEventListener) {
    reducedMotion.addEventListener('change', handleMotionChange);
  } else {
    reducedMotion.addListener(handleMotionChange);
  }

  // Live session card: a synthetic run replayed in a loop. Numbers are invented.
  class LiveDemo {
    constructor(root) {
      this.root = root;
      this.fields = Object.fromEntries(
        [...root.querySelectorAll('[data-live]')].map(node => [node.dataset.live, node]),
      );
      this.series = LiveDemo.buildSeries();
      this.playMs = 15000;
      this.holdMs = 4500;
      this.elapsed = 0;
      this.last = 0;
      this.frame = 0;
      this.visible = true;
      this.painted = -1;
      this.render(this.series.length);

      if ('IntersectionObserver' in window) {
        new IntersectionObserver(entries => {
          this.visible = entries[0]?.isIntersecting ?? true;
          if (this.visible) this.start();
          else this.stop();
        }, { threshold: 0.15 }).observe(root);
      }
      this.start();
    }

    static buildSeries() {
      let seed = 11;
      const random = () => {
        seed = (seed * 16807) % 2147483647;
        return seed / 2147483647;
      };
      const count = 31;
      const contextWindow = 200000;
      const rows = [];
      let context = 38000;
      let cost = 0;
      let tokens = 0;
      let cached = 0;
      let input = 0;
      for (let index = 0; index < count; index += 1) {
        context += 2400 + random() * 4200;
        const write = index === 0 ? context * 0.9 : (index === 12 || index === 23 ? 9000 : 600 + random() * 900);
        const step = 0.034 + random() * 0.03 + (index === 0 ? 0.22 : 0) + (index === 12 || index === 23 ? 0.1 : 0);
        cost += step;
        tokens += context + 900 + random() * 1600;
        input += context;
        cached += Math.max(0, context - write);
        rows.push({ step, cost, tokens, context, cache: cached / input, contextWindow });
      }
      const scaleCost = 2.41 / cost;
      const scaleContext = 142000 / context;
      const scaleTokens = 2790000 / tokens;
      return rows.map(row => ({
        step: row.step * scaleCost,
        cost: row.cost * scaleCost,
        tokens: row.tokens * scaleTokens,
        context: row.context * scaleContext,
        cache: row.cache,
        contextWindow: row.contextWindow,
      }));
    }

    static tokens(value) {
      if (value >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
      return `${Math.round(value / 1000)}k`;
    }

    render(done) {
      const count = Math.max(1, Math.min(this.series.length, done));
      if (count === this.painted) return;
      this.painted = count;
      const row = this.series[count - 1];
      const percent = Math.round((row.context / row.contextWindow) * 100);
      const set = (key, text) => {
        if (this.fields[key]) this.fields[key].textContent = text;
      };
      set('cost', `$${row.cost.toFixed(2)}`);
      set('tokens', LiveDemo.tokens(row.tokens));
      set('cache', `${Math.round(row.cache * 100)}%`);
      set('execs', String(count));
      set('ctx', `${percent}% · ${LiveDemo.tokens(row.context)}`);
      // Same context thresholds and labels as the product's menu-bar recommendation.
      const level = percent >= 70 ? 'high' : percent >= 65 ? 'watch' : 'good';
      set('status', { high: 'Summarize soon', watch: 'Watch context', good: 'Let it run' }[level]);
      this.root.dataset.level = level;
      this.fields.meter?.style.setProperty('--ctx', `${percent}%`);
      const spark = this.fields.spark;
      if (spark) {
        const peak = Math.max(...this.series.map(item => item.step));
        const width = 280;
        const height = 44;
        const stepX = width / (this.series.length - 1);
        spark.setAttribute('points', this.series.slice(0, count).map((item, index) => {
          const y = height - 3 - (item.step / peak) * (height - 6);
          return `${(index * stepX).toFixed(1)},${y.toFixed(1)}`;
        }).join(' '));
      }
    }

    tick(time) {
      this.frame = 0;
      if (!this.visible || document.hidden || reducedMotion.matches) return;
      this.elapsed += Math.min(100, time - (this.last || time));
      this.last = time;
      const cycle = this.playMs + this.holdMs;
      const position = this.elapsed % cycle;
      const progress = Math.min(1, position / this.playMs);
      this.render(1 + Math.floor(progress * (this.series.length - 1)));
      this.frame = requestAnimationFrame(next => this.tick(next));
    }

    start() {
      if (this.frame || !this.visible || document.hidden || reducedMotion.matches) return;
      this.last = 0;
      this.frame = requestAnimationFrame(time => this.tick(time));
    }

    stop() {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
    }

    settle() {
      this.stop();
      this.render(this.series.length);
    }
  }

  const liveDemos = [...document.querySelectorAll('[data-live-demo]')]
    .map(root => new LiveDemo(root));

  document.addEventListener('visibilitychange', () => {
    liveDemos.forEach(demo => document.hidden ? demo.stop() : demo.start());
  });

  const settleLiveDemos = event => {
    liveDemos.forEach(demo => event.matches ? demo.settle() : demo.start());
  };
  if (reducedMotion.addEventListener) {
    reducedMotion.addEventListener('change', settleLiveDemos);
  } else {
    reducedMotion.addListener(settleLiveDemos);
  }

})();
