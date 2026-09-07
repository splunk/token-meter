(() => {
  'use strict';

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const header = document.querySelector('.site-header');
  const updateHeader = () => header?.classList.toggle('is-scrolled', window.scrollY > 72);
  updateHeader();
  window.addEventListener('scroll', updateHeader, { passive: true });

  const revealItems = [...document.querySelectorAll('[data-reveal]')];
  if (reducedMotion.matches || !('IntersectionObserver' in window)) {
    revealItems.forEach(item => item.classList.add('revealed'));
  } else {
    const revealObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('revealed');
        revealObserver.unobserve(entry.target);
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
})();
