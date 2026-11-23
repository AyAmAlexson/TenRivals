import React, { useEffect, useLayoutEffect, useRef, useState } from 'https://esm.sh/react@18.3.1';
import { createPortal } from 'https://esm.sh/react-dom@18.3.1';
import { createRoot } from 'https://esm.sh/react-dom@18.3.1/client';
import htm from 'https://esm.sh/htm@3.1.1';

const html = htm.bind(React.createElement);
const data = JSON.parse(document.getElementById('home-preview-data').textContent);

const CAROUSEL_STIFFNESS = 150;
const CAROUSEL_DAMPING = 24;
const clampNumber = (value, min, max) => Math.min(max, Math.max(min, value));
const wrapNumber = (value, count) => ((value % count) + count) % count;
const nearestNumber = (offset, count) => wrapNumber(offset + count / 2, count) - count / 2;
const rubberBand = (value, max) => {
  if (value < 0) return -0.3 * (1 - Math.exp(value / 0.55));
  if (value > max) return max + 0.3 * (1 - Math.exp((max - value) / 0.55));
  return value;
};

function Chevron({ flip }) {
  return html`<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style=${flip ? { transform: 'scaleX(-1)' } : undefined}><path d="M14.5 6.5 9 12l5.5 5.5" /></svg>`;
}

function BitsCarousel({
  mode = 'fade',
  autoplay = 0,
  loop = false,
  surface = 'light',
  accent = '',
  portalTo = '',
  className = '',
  slideClass = '',
  prevLabel = 'Previous',
  nextLabel = 'Next',
  controlsLabel = 'Carousel',
  children,
}) {
  const items = React.Children.toArray(children);
  const count = items.length;
  const rootRef = useRef(null);
  const viewportRef = useRef(null);
  const trackRef = useRef(null);
  const controlsRef = useRef(null);
  const slideRefs = useRef([]);
  const fillRefs = useRef([]);
  const apiRef = useRef({ step() {}, go() {} });
  const [active, setActive] = useState(0);
  const [stops, setStops] = useState(Math.max(1, count));
  const [host, setHost] = useState(null);
  const barCount = mode === 'fade' ? count : stops;

  useLayoutEffect(() => {
    if (!portalTo) {
      setHost(null);
      return undefined;
    }
    setHost(document.getElementById(portalTo));
    return undefined;
  }, [portalTo]);

  useEffect(() => {
    const viewport = viewportRef.current;
    const root = rootRef.current;
    if (!viewport || !root || count < 2) return undefined;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const metrics = { span: 1, max: Math.max(0, count - 1), visible: 1 };
    const state = {
      p: 0,
      v: 0,
      target: 0,
      shown: 0,
      elapsed: 0,
      hovered: false,
      dragging: null,
      suppress: false,
      visible: true,
      last: 0,
    };
    let raf = 0;
    let alive = true;
    const maxIndex = () => (mode === 'row' ? metrics.max : count - 1);
    const unit = () => (mode === 'fade' ? Math.max(1, viewport.clientWidth) : Math.max(1, metrics.span));

    const measure = () => {
      if (mode !== 'row') return;
      const track = trackRef.current;
      const first = track && track.firstElementChild;
      if (!first) return;
      const gap = parseFloat(getComputedStyle(track).columnGap) || 8;
      const cell = first.getBoundingClientRect().width;
      metrics.span = Math.max(1, cell + gap);
      metrics.visible = Math.max(1, Math.floor((viewport.clientWidth + gap) / metrics.span));
      metrics.max = Math.max(0, count - metrics.visible);
      const nextStops = metrics.max + 1;
      setStops((current) => (current === nextStops ? current : nextStops));
      if (state.target > metrics.max) {
        state.target = metrics.max;
        state.p = reduceMotion() ? metrics.max : state.p;
      }
    };
    const reduceMotion = () => media.matches;

    const paint = () => {
      if (mode === 'fade') {
        const width = viewport.clientWidth || 1;
        for (let i = 0; i < count; i += 1) {
          const slide = slideRefs.current[i];
          if (!slide) continue;
          const offset = loop ? nearestNumber(i - state.p, count) : i - state.p;
          const near = Math.min(1, Math.abs(offset));
          const fade = 1 - near * near * (3 - 2 * near);
          const onScreen = Math.abs(offset) < 1.02;
          slide.style.opacity = String(onScreen ? fade : 0);
          slide.style.visibility = onScreen ? 'visible' : 'hidden';
          slide.style.transform = `translate3d(${offset * width * 0.14}px,0,0) scale(${1 - 0.05 * near})`;
          slide.style.pointerEvents = fade > 0.65 ? 'auto' : 'none';
        }
      } else if (trackRef.current) {
        trackRef.current.style.transform = `translate3d(${-state.p * metrics.span}px,0,0)`;
      }
      const bars = mode === 'row' ? metrics.max + 1 : count;
      const progress = autoplay ? Math.min(1, state.elapsed / autoplay) : 1;
      for (let i = 0; i < bars; i += 1) {
        const fill = fillRefs.current[i];
        if (!fill) continue;
        const scale = i < state.shown ? 1 : i === state.shown ? progress : 0;
        fill.style.transform = `scaleX(${scale})`;
      }
    };

    const announce = () => {
      const index = loop && mode === 'fade'
        ? wrapNumber(Math.round(state.target), count)
        : clampNumber(Math.round(state.target), 0, maxIndex());
      if (index === state.shown) return;
      state.shown = index;
      setActive(index);
    };

    const tick = (now) => {
      raf = 0;
      if (!alive) return;
      const dt = state.last ? Math.min(0.05, (now - state.last) / 1000) : 1 / 60;
      state.last = now;
      let busy = false;
      if (!state.dragging?.moved) {
        if (reduceMotion()) {
          state.p = state.target;
          state.v = 0;
        } else {
          const steps = Math.max(1, Math.ceil(dt * 240));
          const h = dt / steps;
          for (let k = 0; k < steps; k += 1) {
            state.v += (CAROUSEL_STIFFNESS * (state.target - state.p) - CAROUSEL_DAMPING * state.v) * h;
            state.p += state.v * h;
          }
          if (Math.abs(state.target - state.p) < 0.0008 && Math.abs(state.v) < 0.004) {
            state.p = state.target;
            state.v = 0;
            if (loop && mode === 'fade') {
              state.target = wrapNumber(Math.round(state.target), count);
              state.p = state.target;
            }
          } else busy = true;
        }
      }
      const paused = state.hovered || state.dragging || !state.visible || document.hidden;
      if (autoplay && !paused) {
        state.elapsed += dt * 1000;
        if (state.elapsed >= autoplay) step(1);
        busy = true;
      }
      paint();
      if (busy && !raf) raf = requestAnimationFrame(tick);
      else if (!busy) state.last = 0;
    };
    const wake = () => {
      if (!raf && alive) raf = requestAnimationFrame(tick);
    };
    const settle = (target) => {
      state.target = target;
      state.elapsed = 0;
      announce();
      paint();
      wake();
    };
    const step = (direction) => {
      let next = Math.round(state.target) + direction;
      const max = maxIndex();
      if (!(loop && mode === 'fade')) {
        if (autoplay && next > max) next = 0;
        else next = clampNumber(next, 0, max);
      }
      settle(next);
    };
    const go = (index) => {
      const max = maxIndex();
      const goal = clampNumber(Math.round(index), 0, max);
      if (loop && mode === 'fade') {
        const current = Math.round(state.target);
        settle(current + nearestNumber(goal - wrapNumber(current, count), count));
      } else settle(goal);
    };
    apiRef.current = { step, go };

    const onDown = (event) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      state.dragging = {
        id: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        p: state.p,
        from: Math.round(state.target),
        moved: false,
        samples: [],
      };
    };
    const onMove = (event) => {
      const drag = state.dragging;
      if (!drag || event.pointerId !== drag.id) return;
      if (!drag.moved) {
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return;
        if (Math.abs(dy) > Math.abs(dx)) {
          state.dragging = null;
          return;
        }
        drag.moved = true;
        drag.x = event.clientX;
        drag.p = state.p;
        viewport.setPointerCapture?.(event.pointerId);
        root.classList.add('is-dragging');
      }
      const raw = drag.p - (event.clientX - drag.x) / unit();
      const max = maxIndex();
      state.p = loop && mode === 'fade' ? raw : rubberBand(raw, max);
      state.v = 0;
      state.target = Math.round(loop && mode === 'fade' ? state.p : clampNumber(state.p, 0, max));
      drag.samples.push([event.timeStamp, event.clientX]);
      if (drag.samples.length > 8) drag.samples.shift();
      announce();
      paint();
    };
    const onUp = (event) => {
      const drag = state.dragging;
      if (!drag || event.pointerId !== drag.id) return;
      state.dragging = null;
      root.classList.remove('is-dragging');
      if (!drag.moved) {
        wake();
        return;
      }
      state.suppress = true;
      window.setTimeout(() => {
        state.suppress = false;
      }, 40);
      const sample = drag.samples.find(([time]) => event.timeStamp - time < 90) || [event.timeStamp, event.clientX];
      const elapsed = Math.max(16, event.timeStamp - sample[0]);
      const velocity = clampNumber(((sample[1] - event.clientX) / elapsed) * (1000 / unit()), -12, 12);
      const travelled = state.p - drag.from;
      let target = Math.round(state.p + velocity * 0.22);
      if (target === drag.from) {
        if (Math.abs(velocity) > 0.8) target += Math.sign(velocity);
        else if (Math.abs(travelled) > 0.18) target += Math.sign(travelled);
      }
      if (!(loop && mode === 'fade')) target = clampNumber(target, 0, maxIndex());
      const inside = (loop && mode === 'fade') || (state.p >= 0 && state.p <= maxIndex());
      state.v = inside && !reduceMotion() ? velocity : 0;
      settle(target);
    };
    const onClick = (event) => {
      if (!state.suppress) return;
      event.preventDefault();
      event.stopPropagation();
    };
    const onEnter = () => {
      state.hovered = true;
    };
    const onLeave = () => {
      state.hovered = false;
      wake();
    };
    const onKey = (event) => {
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        step(1);
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        step(-1);
      }
    };
    const onVisible = () => {
      if (!document.hidden) wake();
    };

    measure();
    paint();
    const observer = new ResizeObserver(() => {
      measure();
      paint();
    });
    observer.observe(viewport);
    let seen;
    if (typeof IntersectionObserver !== 'undefined') {
      seen = new IntersectionObserver((entries) => {
        state.visible = entries.some((entry) => entry.isIntersecting);
        if (state.visible) wake();
      }, { threshold: 0.2 });
      seen.observe(root);
    }
    viewport.addEventListener('pointerdown', onDown);
    viewport.addEventListener('pointermove', onMove);
    viewport.addEventListener('pointerup', onUp);
    viewport.addEventListener('pointercancel', onUp);
    viewport.addEventListener('click', onClick, true);
    root.addEventListener('pointerenter', onEnter);
    root.addEventListener('pointerleave', onLeave);
    root.addEventListener('keydown', onKey);
    controlsRef.current?.addEventListener('pointerenter', onEnter);
    controlsRef.current?.addEventListener('pointerleave', onLeave);
    document.addEventListener('visibilitychange', onVisible);
    if (autoplay) wake();

    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      observer.disconnect();
      seen?.disconnect();
      root.classList.remove('is-dragging');
      viewport.removeEventListener('pointerdown', onDown);
      viewport.removeEventListener('pointermove', onMove);
      viewport.removeEventListener('pointerup', onUp);
      viewport.removeEventListener('pointercancel', onUp);
      viewport.removeEventListener('click', onClick, true);
      root.removeEventListener('pointerenter', onEnter);
      root.removeEventListener('pointerleave', onLeave);
      root.removeEventListener('keydown', onKey);
      controlsRef.current?.removeEventListener('pointerenter', onEnter);
      controlsRef.current?.removeEventListener('pointerleave', onLeave);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [count, mode, autoplay, loop]);

  const controls = barCount > 1 ? html`
    <div
      className=${`bits__controls bits__controls--${surface}`}
      ref=${controlsRef}
      style=${accent ? { '--bits-accent': accent } : undefined}
      aria-label=${controlsLabel}
    >
      <button type="button" className="bits__arrow" aria-label=${prevLabel} disabled=${!loop && active <= 0} onClick=${() => apiRef.current.step(-1)}>
        <${Chevron} />
      </button>
      <div className="bits__bars">
        ${Array.from({ length: barCount }, (_, i) => html`
          <button
            key=${i}
            type="button"
            className=${`bits__bar${i === active ? ' is-active' : ''}`}
            aria-label=${`Go to slide ${i + 1}`}
            aria-current=${i === active ? 'true' : undefined}
            onClick=${() => apiRef.current.go(i)}
          >
            <span className="bits__bar-track"><span className="bits__bar-fill" ref=${(node) => { fillRefs.current[i] = node; }}></span></span>
          </button>
        `)}
      </div>
      <button type="button" className="bits__arrow" aria-label=${nextLabel} disabled=${!loop && active >= barCount - 1} onClick=${() => apiRef.current.step(1)}>
        <${Chevron} flip=${true} />
      </button>
    </div>
  ` : null;
  return html`
    <div className=${`bits bits--${mode} bits--${surface} ${className}`} ref=${rootRef} aria-roledescription="carousel">
      <div className="bits__viewport" ref=${viewportRef}>
        ${mode === 'row'
          ? html`<div className="bits-rail__track product-rail__track" ref=${trackRef}>${items}</div>`
          : items.map((item, i) => html`
            <div
              key=${item.key ?? i}
              className=${`bits__slide ${slideClass}${i === active ? ' is-current' : ''}`}
              ref=${(node) => { slideRefs.current[i] = node; }}
            >${item}</div>
          `)}
      </div>
      ${host && controls ? createPortal(controls, host) : controls}
    </div>
  `;
}

function Picture({ image }) {
  if (!image) return null;
  return html`<img
    src=${image.src}
    srcSet=${image.srcset || undefined}
    sizes=${image.sizes || undefined}
    alt=${image.alt || ''}
    width=${image.width || undefined}
    height=${image.height || undefined}
    loading=${image.priority === 'lazy' ? 'lazy' : undefined}
    fetchPriority=${image.priority === 'high' || image.priority === 'low' ? image.priority : undefined}
    decoding="async"
  />`;
}

function Linked({ link, children }) {
  if (!link) return children;
  return html`<a
    href=${link.href}
    target=${link.external ? '_blank' : undefined}
    rel=${link.external ? 'noopener' : undefined}
  >${children}</a>`;
}

function Headline({ text }) {
  const parts = String(text || '').split(/\r?\n/);
  return html`<h1 className="hero-tagline">${parts.map((line, i) => html`<${React.Fragment} key=${i}>${i ? html`<br />` : null}${line}</${React.Fragment}>`)}</h1>`;
}

function HeroSticker({ src }) {
  const stickerRef = useRef(null);
  const canvasRef = useRef(null);

  useEffect(() => {
    const sticker = stickerRef.current;
    const canvas = canvasRef.current;
    if (!sticker || !canvas || !src) return undefined;
    const ctx = canvas.getContext('2d');
    const image = new Image();
    image.decoding = 'async';
    image.src = src;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const drawnRadius = 0.4;
    let peel = 0;
    let peelV = 0;
    let angle = -Math.PI / 4;
    let angleTarget = angle;
    let target = 0;
    let spin = 0;
    let spinTarget = 0;
    let raf = 0;
    let last = 0;
    let dragging = false;
    let userPlaced = false;
    let grabDX = 0;
    let grabDY = 0;
    let lastPX = 0;
    let lastPT = 0;

    const resize = () => {
      const size = canvas.clientWidth || 1;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(size * dpr);
      canvas.height = Math.round(size * dpr);
      draw();
    };

    const place = () => {
      if (userPlaced) return;
      const hero = sticker.closest('.hero');
      const column = document.querySelector('.shop-home-mid .container')
        || document.querySelector('.shop-home-main .container')
        || (hero && hero.querySelector('.container'));
      if (!hero || !column) return;
      const heroBox = hero.getBoundingClientRect();
      const columnBox = column.getBoundingClientRect();
      const pad = parseFloat(getComputedStyle(column).paddingRight) || 0;
      const contentRight = columnBox.right - pad;
      const size = sticker.offsetWidth || 1;
      const radius = size * drawnRadius;
      const inset = Math.min(28, Math.max(12, window.innerWidth * 0.022));
      const centerX = contentRight - inset - radius;
      const centerY = heroBox.top + inset + radius;
      sticker.style.left = `${centerX - heroBox.left - size / 2}px`;
      sticker.style.top = `${centerY - heroBox.top - size / 2}px`;
    };

    const draw = () => {
      const w = canvas.width;
      const h = canvas.height;
      const cx = w / 2;
      const cy = h / 2;
      const radius = w * drawnRadius;
      const px = w / (canvas.clientWidth || 1);
      ctx.filter = 'none';
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'source-over';
      ctx.clearRect(0, 0, w, h);

      const peeling = peel > 0.02;
      const curlR = radius * 0.2;
      const depth = peeling ? radius * peel * 0.8 : 0;
      const fold = radius - depth;
      const chord = Math.sqrt(Math.max(0, radius * radius - Math.min(radius, Math.max(-radius, fold)) ** 2));
      const ready = image.complete && image.naturalWidth;
      const lift = dragging ? 18 * px : 0;
      const ox = 1.4 * px + 0.28 * lift;
      const oy = 3.2 * px + 0.55 * lift;

      const traceSide = (keep, edge = fold) => {
        ctx.beginPath();
        if (!peeling || edge >= radius - 0.5) {
          if (keep === 'flat') ctx.arc(0, 0, radius, 0, Math.PI * 2);
          return;
        }
        if (edge <= -radius + 0.5) {
          if (keep === 'peeled') ctx.arc(0, 0, radius, 0, Math.PI * 2);
          return;
        }
        const span = Math.sqrt(Math.max(0, radius * radius - edge * edge));
        const sweep = Math.atan2(span, edge);
        ctx.arc(0, 0, radius, -sweep, sweep, keep === 'flat');
        ctx.closePath();
      };

      ctx.save();
      ctx.translate(cx, cy);
      ctx.filter = `blur(${((5 + (dragging ? 2.2 : 0)) * px).toFixed(2)}px)`;
      ctx.translate(ox, oy);
      ctx.rotate(angle);
      ctx.globalAlpha = 0.24 / (1 + lift * 0.01);
      ctx.fillStyle = '#000';
      traceSide('flat', Math.min(fold, radius) - (peeling ? curlR * 1.8 : 0));
      ctx.fill();
      ctx.restore();

      if (peeling && chord > 1.5 && fold < radius - 0.5) {
        ctx.save();
        ctx.translate(cx, cy);
        ctx.filter = `blur(${(4.5 * px).toFixed(2)}px)`;
        ctx.translate(1.8 * px, oy);
        ctx.rotate(angle);
        ctx.globalAlpha = 0.32;
        ctx.fillStyle = '#000';
        traceSide('peeled');
        ctx.fill();
        ctx.restore();
      }

      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.clip();
      ctx.translate(cx, cy);
      ctx.rotate(angle);
      if (ready) {
        ctx.save();
        traceSide('flat');
        ctx.clip();
        ctx.rotate(-angle);
        ctx.drawImage(image, -radius, -radius, radius * 2, radius * 2);
        ctx.restore();
      }
      if (peeling && chord > 1.5 && Math.abs(fold) < radius - 0.5) {
        const arcStart = Math.atan2(-chord, -fold);
        const arcEnd = Math.atan2(chord, -fold);
        const turn = (value) => {
          const t = Math.atan2(Math.sin(value), Math.cos(value));
          return t < 0 ? t + Math.PI * 2 : t;
        };
        const arcHoldsTip = (anticlockwise) => {
          const s = turn(arcStart);
          const e = turn(arcEnd);
          const tip = Math.PI;
          if (anticlockwise) return s >= e ? tip <= s && tip >= e : tip <= s || tip >= e;
          return s <= e ? tip >= s && tip <= e : tip >= s || tip <= e;
        };
        const curlArc = arcHoldsTip(true);
        const tipX = fold * 2 - radius;
        ctx.save();
        ctx.beginPath();
        ctx.arc(fold * 2, 0, radius, arcStart, arcEnd, curlArc);
        ctx.closePath();
        ctx.clip();
        const paper = ctx.createLinearGradient(fold, 0, tipX, 0);
        paper.addColorStop(0, '#9a9aa1');
        paper.addColorStop(0.35, '#d0d0d4');
        paper.addColorStop(0.78, '#ececee');
        paper.addColorStop(1, '#f7f7f8');
        ctx.fillStyle = paper;
        ctx.fillRect(-radius * 2, -radius * 2, radius * 4, radius * 4);
        const sky = ctx.createLinearGradient(0, -chord, 0, chord);
        sky.addColorStop(0, 'rgba(255,255,255,0.34)');
        sky.addColorStop(0.42, 'rgba(255,255,255,0)');
        sky.addColorStop(1, 'rgba(0,0,0,0.24)');
        ctx.fillStyle = sky;
        ctx.fillRect(-radius * 2, -radius * 2, radius * 4, radius * 4);
        const crease = ctx.createLinearGradient(fold, 0, fold + (tipX - fold) * 0.28, 0);
        crease.addColorStop(0, 'rgba(0,0,0,0.22)');
        crease.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = crease;
        ctx.fillRect(-radius * 2, -radius * 2, radius * 4, radius * 4);
        ctx.restore();
        ctx.beginPath();
        ctx.arc(fold * 2, 0, radius, arcStart, arcEnd, curlArc);
        ctx.strokeStyle = 'rgba(255,255,255,0.9)';
        ctx.lineWidth = Math.max(1.2, px);
        ctx.stroke();
      }
      ctx.restore();
      sticker.style.transform = `rotate(${30 + spin}deg) scale(${1 + peel * 0.08})`;
    };

    const step = (now) => {
      raf = 0;
      if (!last) last = now;
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const pull = 1 - Math.exp(-dt / 0.14);
      peelV += ((target - peel) * 90 - peelV * 16) * dt;
      peel = Math.max(0, Math.min(1, peel + peelV * dt));
      let delta = angleTarget - angle;
      delta = Math.atan2(Math.sin(delta), Math.cos(delta));
      angle += delta * pull;
      spin += (spinTarget - spin) * pull;
      try {
        draw();
      } catch (error) {
        sticker.dataset.peelError = String(error);
        return;
      }
      const idle = Math.abs(peel - target) < 0.004
        && Math.abs(peelV) < 0.004
        && Math.abs(delta) < 0.01
        && Math.abs(spin - spinTarget) < 0.05;
      if (!idle) raf = requestAnimationFrame(step);
    };
    const wake = () => {
      if (!raf) raf = requestAnimationFrame(step);
    };
    const localPoint = (event) => {
      const rect = sticker.getBoundingClientRect();
      const rot = (30 + spin) * Math.PI / 180;
      const cos = Math.cos(rot);
      const sin = Math.sin(rot);
      const dx = event.clientX - (rect.left + rect.width / 2);
      const dy = event.clientY - (rect.top + rect.height / 2);
      return {
        x: dx * cos + dy * sin,
        y: -dx * sin + dy * cos,
      };
    };
    const aim = (event, amount) => {
      const point = localPoint(event);
      if (point.x * point.x + point.y * point.y > 9) angleTarget = Math.atan2(point.y, point.x);
      target = amount;
      if (amount > 0.9) peel = Math.max(peel, 0.82);
      if (media.matches) {
        peel = amount;
        angle = angleTarget;
        spin = spinTarget;
      }
      draw();
      if (!media.matches) wake();
    };
    const onEnter = (event) => {
      if (dragging) return;
      aim(event, 0.72);
    };
    const onMove = (event) => {
      if (!dragging) {
        if (media.matches) return;
        aim(event, 0.72);
        return;
      }
      const hero = sticker.closest('.hero');
      const heroBox = hero.getBoundingClientRect();
      const size = sticker.offsetWidth || 1;
      const centerX = event.clientX - grabDX - heroBox.left;
      const centerY = event.clientY - grabDY - heroBox.top;
      const slack = size * 0.2;
      sticker.style.left = `${Math.min(hero.clientWidth - size + slack, Math.max(-slack, centerX - size / 2))}px`;
      sticker.style.top = `${Math.min(hero.clientHeight - size + slack, Math.max(-slack, centerY - size / 2))}px`;
      const now = performance.now();
      const vx = (event.clientX - lastPX) / Math.max(16, now - lastPT);
      lastPX = event.clientX;
      lastPT = now;
      spinTarget = Math.max(-12, Math.min(12, vx * -18));
      userPlaced = true;
      aim(event, 1);
    };
    const onDown = (event) => {
      if (event.button !== 0) return;
      dragging = true;
      const rect = sticker.getBoundingClientRect();
      grabDX = event.clientX - (rect.left + rect.width / 2);
      grabDY = event.clientY - (rect.top + rect.height / 2);
      lastPX = event.clientX;
      lastPT = performance.now();
      sticker.classList.add('is-lifted');
      try {
        sticker.setPointerCapture?.(event.pointerId);
      } catch (error) {
        /* Synthetic pointers have no capture; a real press still captures. */
      }
      event.preventDefault();
      aim(event, 1);
    };
    const onUp = () => {
      if (!dragging) return;
      dragging = false;
      spinTarget = 0;
      target = 0;
      sticker.classList.remove('is-lifted');
      wake();
    };
    const onLeave = () => {
      if (dragging) return;
      target = 0;
      wake();
    };

    image.onload = () => draw();
    const observer = new ResizeObserver(() => {
      resize();
      place();
    });
    observer.observe(canvas);
    const hero = sticker.closest('.hero');
    if (hero) observer.observe(hero);
    resize();
    place();
    sticker.addEventListener('pointerenter', onEnter);
    sticker.addEventListener('pointermove', onMove);
    sticker.addEventListener('pointerdown', onDown);
    sticker.addEventListener('pointerup', onUp);
    sticker.addEventListener('pointercancel', onUp);
    sticker.addEventListener('pointerleave', onLeave);
    if (image.complete) draw();

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      sticker.removeEventListener('pointerenter', onEnter);
      sticker.removeEventListener('pointermove', onMove);
      sticker.removeEventListener('pointerdown', onDown);
      sticker.removeEventListener('pointerup', onUp);
      sticker.removeEventListener('pointercancel', onUp);
      sticker.removeEventListener('pointerleave', onLeave);
    };
  }, [src]);

  return html`
    <div className="hero-sticker" ref=${stickerRef}>
      <canvas ref=${canvasRef} aria-hidden="true"></canvas>
    </div>
  `;
}

function Hero({ hero, labels }) {
  const slides = hero.slides || [];
  return html`
    <section className="hero" id="shop-hero">
      ${slides.length
        ? html`<${BitsCarousel}
            mode="fade"
            autoplay=${7000}
            loop=${true}
            surface="dark"
            portalTo="shop-hero"
            className="hero-carousel"
            slideClass="hero-slide"
            prevLabel=${labels.prevHero}
            nextLabel=${labels.nextHero}
            controlsLabel=${labels.prevHero}
          >
            ${slides.map((item, i) => html`
              <${Linked} key=${i} link=${item.link}><${Picture} image=${item.image} /></${Linked}>
            `)}
          </${BitsCarousel}>`
        : html`<div className="hero-carousel" aria-hidden="true"><div className="hero-slide hero-slide--placeholder is-current">${labels.heroPlaceholder}</div></div>`}
      <div className="hero-scrim" aria-hidden="true"></div>
      <div className="container hero-inner">
        <div className="hero-content">
          <${Headline} text=${hero.headline} />
          ${hero.subtext ? html`<p className="hero-sub">${hero.subtext}</p>` : null}
          <div className="hero-cta-row">
            ${hero.cta ? html`<a className="btn hero-cta-primary" href=${hero.cta.href}>${hero.cta.label}</a>` : null}
            ${hero.secondary ? html`<a className="hero-cta-secondary" href=${hero.secondary.href}>${hero.secondary.label}</a>` : null}
          </div>
        </div>
      </div>
      ${hero.sticker ? html`<${HeroSticker} src=${hero.sticker} />` : null}
    </section>
  `;
}

function ProductTile({ product, labels }) {
  const price = product.price || {};
  return html`
    <div className="product-tile">
      ${product.edit ? html`
        <a href=${product.edit} className="product-tile__edit" title=${labels.editProduct} aria-label=${labels.editProduct}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 013 3L8 18l-4 1 1-4L16.5 3.5z" /></svg>
        </a>
      ` : null}
      <a className="product-tile__link" href=${product.href}>
        <div className="product-tile__media">
          ${product.image
            ? html`<${Picture} image=${product.image} />`
            : html`<svg width="48" height="48" viewBox="0 0 48 48" fill="none" aria-hidden="true" style=${{ opacity: 0.35 }}><rect x="8" y="12" width="32" height="28" rx="2" stroke="currentColor" strokeWidth="1.5" /><path d="M8 32l8-8 6 6 8-10 10 12" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /><circle cx="18" cy="20" r="3" stroke="currentColor" strokeWidth="1.5" /></svg>`}
        </div>
        <div className="product-tile__meta">
          <div className="product-tile__body">
            ${product.brand ? html`<p className="product-tile__brand">${product.brand}</p>` : null}
            <p className="product-tile__model">${product.name}</p>
            ${product.color ? html`<p className="product-tile__color">${product.color}</p>` : null}
            ${product.short ? html`<p className="product-tile__short">${product.short}</p>` : null}
          </div>
          <div className="product-tile__footer">
            <p className="product-tile__type">${product.type}</p>
            <div className="product-tile__prices">
              ${price.kind === 'sale' ? html`
                <span className="product-tile__price product-tile__price--sale">${price.current} ₾</span>
                <span className="product-tile__price-old">${price.old} ₾</span>
              ` : price.kind === 'regular' ? html`
                <span className="product-tile__price">${price.current} ₾</span>
              ` : html`<span className="product-tile__price-muted">${price.label || labels.comingSoon}</span>`}
            </div>
          </div>
        </div>
      </a>
    </div>
  `;
}

const RAIL_ACCENT = {
  red: '#B20009',
  teal: '#0f766e',
  blue: '#1e40af',
  navy: '#141c2c',
  magenta: '#be185d',
};

function Rail({ controlsLabel, prevLabel, nextLabel, tone, children }) {
  return html`
    <${BitsCarousel}
      mode="row"
      surface="light"
      accent=${RAIL_ACCENT[tone] || ''}
      className="product-rail-wrap"
      prevLabel=${prevLabel}
      nextLabel=${nextLabel}
      controlsLabel=${controlsLabel}
    >${children}</${BitsCarousel}>
  `;
}

function SectionHead({ title, href, viewAll }) {
  return html`
    <div className="section-head">
      <h2>${title}</h2>
      <a href=${href}>${viewAll} →</a>
    </div>
  `;
}

function Promo({ promos }) {
  const slides = promos.slides || [];
  if (!slides.length) return null;
  return html`
    <section className="section promo-band">
      <${BitsCarousel}
        mode="fade"
        autoplay=${7000}
        loop=${true}
        surface="dark"
        className="promo-carousel bits--overlay"
        slideClass="promo-carousel__slide"
        prevLabel=${promos.prev}
        nextLabel=${promos.next}
        controlsLabel=${promos.controls}
      >
        ${slides.map((item, i) => html`
          <${Linked} key=${i} link=${item.link}><${Picture} image=${item.image} /></${Linked}>
        `)}
      </${BitsCarousel}>
    </section>
  `;
}

function Stories({ stories, labels }) {
  if (!stories.items.length) return null;
  return html`
    <section className="section featured-stories-block">
      <${SectionHead} title=${stories.title} href=${stories.viewAll} viewAll=${labels.viewAll} />
      <${Rail} controlsLabel=${stories.controls} prevLabel=${stories.prev} nextLabel=${stories.next} tone=${stories.tone}>
        ${stories.items.map((post) => html`
          <div key=${post.href} className="product-rail__cell story-rail__cell">
            <a className="story-card" href=${post.href}>
              ${post.image
                ? html`<div className="story-card__media"><${Picture} image=${post.image} /></div>`
                : html`<div className="story-card__media" style=${{ minHeight: '160px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#94a3b8', fontSize: '11px', fontWeight: 800, letterSpacing: '.08em', textTransform: 'uppercase' }}>${labels.storyFallback}</div>`}
              <div className="story-card__body">
                <h3 className="story-card__title">${post.title}</h3>
                ${post.lead ? html`<p className="story-card__caption">${post.lead}</p>` : null}
                <span className="story-card__cta">${post.cta} →</span>
              </div>
            </a>
          </div>
        `)}
      </${Rail}>
    </section>
  `;
}

function LogoLoop({ items, label }) {
  const rootRef = useRef(null);
  const trackRef = useRef(null);
  const [copies, setCopies] = useState(2);
  const itemsKey = items.map((item) => item.src).join('|');

  useEffect(() => {
    const root = rootRef.current;
    const track = trackRef.current;
    if (!root || !track || !items.length) return undefined;

    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const speed = 72;
    let raf = 0;
    let offset = 0;
    let velocity = 0;
    let fling = 0;
    let last = 0;
    let hovering = false;
    let visible = true;
    let dragging = false;
    let moved = false;
    let pointerId = null;
    let grabX = 0;
    let grabOffset = 0;
    let lastX = 0;
    let lastT = 0;

    const sequenceWidth = () => {
      const list = track.querySelector('.logo-loop__list');
      return list ? list.getBoundingClientRect().width : 0;
    };
    const wrap = (value, period) => {
      if (!period) return 0;
      const mod = value % period;
      return mod < 0 ? mod + period : mod;
    };
    const paint = () => {
      offset = wrap(offset, sequenceWidth());
      track.style.transform = `translate3d(${-offset}px,0,0)`;
    };
    const fit = () => {
      const width = sequenceWidth();
      if (!width) return;
      const need = Math.max(2, Math.ceil(root.clientWidth / width) + 2);
      setCopies((current) => (current === need ? current : need));
    };

    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(root);
    observer.observe(track);

    const frame = (now) => {
      if (!last) last = now;
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (!dragging) {
        if (Math.abs(fling) > 8) {
          offset += fling * dt;
          fling *= Math.exp(-dt / 0.35);
        } else {
          fling = 0;
          const cruise = media.matches || !visible || document.hidden ? 0 : speed;
          const goal = hovering ? 0 : cruise;
          velocity += (goal - velocity) * (1 - Math.exp(-dt / 0.25));
          offset += velocity * dt;
        }
        paint();
      }
      raf = requestAnimationFrame(frame);
    };

    const onEnter = () => { hovering = true; };
    const onLeave = () => { hovering = false; };
    const onDown = (event) => {
      if (event.button !== 0) return;
      dragging = true;
      moved = false;
      pointerId = event.pointerId;
      grabX = lastX = event.clientX;
      lastT = performance.now();
      grabOffset = offset;
      fling = 0;
      velocity = 0;
      root.classList.add('is-dragging');
      root.setPointerCapture?.(pointerId);
    };
    const onMove = (event) => {
      if (!dragging || event.pointerId !== pointerId) return;
      const dx = event.clientX - grabX;
      if (Math.abs(dx) > 6) moved = true;
      offset = grabOffset - dx;
      const now = performance.now();
      fling = ((lastX - event.clientX) / Math.max(16, now - lastT)) * 1000;
      lastX = event.clientX;
      lastT = now;
      paint();
    };
    const endDrag = (event) => {
      if (!dragging || (event.pointerId != null && event.pointerId !== pointerId)) return;
      dragging = false;
      pointerId = null;
      root.classList.remove('is-dragging');
    };
    const onClick = (event) => {
      if (!moved) return;
      event.preventDefault();
      event.stopPropagation();
      moved = false;
    };
    const onDragStart = (event) => event.preventDefault();

    root.addEventListener('pointerenter', onEnter);
    root.addEventListener('pointerleave', onLeave);
    root.addEventListener('pointerdown', onDown);
    root.addEventListener('pointermove', onMove);
    root.addEventListener('pointerup', endDrag);
    root.addEventListener('pointercancel', endDrag);
    root.addEventListener('click', onClick, true);
    root.addEventListener('dragstart', onDragStart);

    let visibility;
    if (typeof IntersectionObserver !== 'undefined') {
      visibility = new IntersectionObserver((entries) => {
        visible = entries[entries.length - 1].isIntersecting;
      }, { threshold: 0.15 });
      visibility.observe(root);
    }

    if (!media.matches) raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      visibility?.disconnect();
      root.classList.remove('is-dragging');
      root.removeEventListener('pointerenter', onEnter);
      root.removeEventListener('pointerleave', onLeave);
      root.removeEventListener('pointerdown', onDown);
      root.removeEventListener('pointermove', onMove);
      root.removeEventListener('pointerup', endDrag);
      root.removeEventListener('pointercancel', endDrag);
      root.removeEventListener('click', onClick, true);
      root.removeEventListener('dragstart', onDragStart);
    };
  }, [itemsKey, items.length]);

  const sequence = (copy) => html`
    <ul className="logo-loop__list" key=${copy} aria-hidden=${copy ? 'true' : undefined}>
      ${items.map((item) => html`
        <li className="logo-loop__item" key=${`${copy}-${item.href}`}>
          <a
            className="logo-loop__link"
            style=${{ '--brand-optical-scale': item.scale }}
            href=${item.href}
            title=${copy ? undefined : item.title}
            tabIndex=${copy ? -1 : undefined}
            draggable="false"
          >
            <img src=${item.src} alt=${copy ? '' : item.alt} width="220" height="56" draggable="false" decoding="async" />
          </a>
        </li>
      `)}
    </ul>
  `;

  return html`
    <div className="logo-loop" ref=${rootRef} aria-label=${label}>
      <div className="logo-loop__track" ref=${trackRef}>
        ${Array.from({ length: copies }, (_, copy) => sequence(copy))}
      </div>
    </div>
  `;
}

function AuroraCta({ aurora }) {
  const rootRef = useRef(null);
  const canvasRef = useRef(null);

  useEffect(() => {
    const root = rootRef.current;
    const canvas = canvasRef.current;
    if (!root || !canvas) return undefined;
    const ctx = canvas.getContext('2d');
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const bands = [
      { hue: 168, y: 0.36, amp: 0.15, freq: 1.25, speed: 0.018, width: 0.24 },
      { hue: 286, y: 0.5, amp: 0.13, freq: 1.7, speed: -0.014, width: 0.2 },
      { hue: 332, y: 0.58, amp: 0.11, freq: 1.05, speed: 0.022, width: 0.16 },
      { hue: 46, y: 0.44, amp: 0.09, freq: 2.05, speed: 0.011, width: 0.12 },
    ];
    let raf = 0;
    let time = 0;
    let aimX = 0.32;
    let aimY = 0.48;
    let visible = true;

    const resize = () => {
      canvas.style.position = 'absolute';
      canvas.style.inset = '0';
      canvas.style.width = '100%';
      canvas.style.height = '100%';
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const width = Math.max(1, root.clientWidth);
      const height = Math.max(240, Math.min(640, root.clientHeight || 240));
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      draw();
    };
    const draw = () => {
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      const sky = ctx.createLinearGradient(0, 0, w, h);
      sky.addColorStop(0, '#07111a');
      sky.addColorStop(0.48, '#12182c');
      sky.addColorStop(1, '#1a0c18');
      ctx.fillStyle = sky;
      ctx.fillRect(0, 0, w, h);
      ctx.globalCompositeOperation = 'lighter';
      bands.forEach((band, index) => {
        ctx.beginPath();
        const steps = 42;
        for (let i = 0; i <= steps; i += 1) {
          const u = i / steps;
          const pull = Math.exp(-(((u - aimX) * 3.1) ** 2)) * (aimY - 0.5) * 0.22;
          const wave = Math.sin(u * band.freq * Math.PI * 2 + time * band.speed + index) * band.amp;
          const y = (band.y + wave + pull) * h;
          if (i === 0) ctx.moveTo(u * w, y);
          else ctx.lineTo(u * w, y);
        }
        ctx.strokeStyle = `hsla(${band.hue}, 78%, 62%, 0.34)`;
        ctx.lineWidth = h * band.width;
        ctx.lineCap = 'round';
        ctx.shadowColor = `hsla(${band.hue}, 90%, 62%, 0.55)`;
        ctx.shadowBlur = h * 0.05;
        ctx.stroke();
      });
      ctx.shadowBlur = 0;
      ctx.globalCompositeOperation = 'source-over';
    };
    const frame = () => {
      time += 1;
      if (visible && !document.hidden) draw();
      raf = requestAnimationFrame(frame);
    };
    const onMove = (event) => {
      const box = root.getBoundingClientRect();
      aimX = (event.clientX - box.left) / Math.max(1, box.width);
      aimY = (event.clientY - box.top) / Math.max(1, box.height);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(root);
    let visibility;
    if (typeof IntersectionObserver !== 'undefined') {
      visibility = new IntersectionObserver((entries) => {
        visible = entries[entries.length - 1].isIntersecting;
      }, { threshold: 0.08 });
      visibility.observe(root);
    }
    root.addEventListener('pointermove', onMove);
    if (!media.matches) raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      visibility?.disconnect();
      root.removeEventListener('pointermove', onMove);
    };
  }, []);

  return html`
    <section className="aurora-cta" ref=${rootRef} aria-label=${aurora.title}>
      <canvas className="aurora-cta__wash" ref=${canvasRef} aria-hidden="true"></canvas>
      <div className="aurora-cta__copy">
        <p className="aurora-cta__kicker">${aurora.kicker}</p>
        <h2>${aurora.title}</h2>
        <p className="aurora-cta__lead">${aurora.lead}</p>
        <div className="aurora-cta__actions">
          <a className="btn" href=${aurora.primary.href}>${aurora.primary.label}</a>
          <a className="aurora-cta__ghost" href=${aurora.secondary.href}>${aurora.secondary.label}</a>
        </div>
      </div>
    </section>
  `;
}

function PointerSheen() {
  useEffect(() => {
    const root = document.querySelector('.shop-home-mid');
    if (!root) return undefined;
    const move = (event) => {
      const card = event.target.closest('.product-tile, .story-card, .cat-seg');
      if (!card || !root.contains(card)) return;
      const box = card.getBoundingClientRect();
      card.style.setProperty('--mx', `${event.clientX - box.left}px`);
      card.style.setProperty('--my', `${event.clientY - box.top}px`);
    };
    root.addEventListener('pointermove', move);
    return () => root.removeEventListener('pointermove', move);
  }, []);
  return null;
}

function Brands({ brands }) {
  if (!brands.items.length) return null;
  return html`
    <section className="brands-strip">
      <div className="container">
        <${SectionHead} title=${brands.title} href=${brands.viewAll} viewAll=${data.labels.viewAll} />
        <${LogoLoop} items=${brands.items} label=${brands.label} />
      </div>
    </section>
  `;
}

function Home() {
  const labels = data.labels;
  return html`
    <${React.Fragment}>
      <${Hero} hero=${data.hero} labels=${labels} />
      <${PointerSheen} />
      <div className="shop-home-mid">
        <div className="announce-bar">${data.announce}</div>
        <div className="cat-strip">
          <div className="container">
            <nav className="cat-rail" aria-label=${labels.categories}>
              ${data.categories.map((item) => html`
                <a key=${item.href} className=${`cat-seg cat-seg--${item.tone}`} href=${item.href}>
                  <span className="cat-seg__label">${item.label}</span>
                </a>
              `)}
            </nav>
          </div>
        </div>
        <main className="shop-home-main">
          <div className="container shop-home-main__inner">
            <section className="section">
              <${SectionHead} title=${data.featured.title} href=${data.featured.viewAll} viewAll=${labels.viewAll} />
              <${Rail} controlsLabel=${data.featured.controls} prevLabel=${data.featured.prev} nextLabel=${data.featured.next} tone=${data.featured.tone}>
                ${data.featured.products.map((product) => html`
                  <div key=${product.href} className="product-rail__cell">
                    <${ProductTile} product=${product} labels=${labels} />
                  </div>
                `)}
              </${Rail}>
            </section>
            <${Promo} promos=${data.promos} />
            <section className="section">
              <${SectionHead} title=${data.arrivals.title} href=${data.arrivals.viewAll} viewAll=${labels.viewAll} />
              <${Rail} controlsLabel=${data.arrivals.controls} prevLabel=${data.arrivals.prev} nextLabel=${data.arrivals.next} tone=${data.arrivals.tone}>
                ${data.arrivals.products.map((product) => html`
                  <div key=${`new-${product.href}`} className="product-rail__cell">
                    <${ProductTile} product=${product} labels=${labels} />
                  </div>
                `)}
              </${Rail}>
            </section>
            <${AuroraCta} aurora=${data.aurora} />
            <${Stories} stories=${data.stories} labels=${labels} />
          </div>
        </main>
      </div>
      <${Brands} brands=${data.brands} />
      <div dangerouslySetInnerHTML=${{ __html: data.aboutHtml }}></div>
    </${React.Fragment}>
  `;
}

createRoot(document.getElementById('home-preview-root')).render(html`<${Home} />`);
