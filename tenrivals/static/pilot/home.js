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
      ctx.clearRect(0, 0, w, h);

      const lift = peel;
      const shadowX = Math.cos(angle) * w * 0.018 * (0.35 + lift);
      const shadowY = Math.sin(angle) * h * 0.012 * lift + h * (0.03 + lift * 0.045);
      ctx.save();
      ctx.translate(cx + shadowX, cy + shadowY);
      const shadow = ctx.createRadialGradient(0, 0, radius * 0.2, 0, 0, radius * (1.05 + lift * 0.32));
      shadow.addColorStop(0, `rgba(0,0,0,${0.32 + lift * 0.3})`);
      shadow.addColorStop(0.68, `rgba(0,0,0,${0.14 + lift * 0.1})`);
      shadow.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = shadow;
      ctx.beginPath();
      ctx.arc(0, 0, radius * (1.05 + lift * 0.32), 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.clip();
      if (image.complete && image.naturalWidth) {
        ctx.drawImage(image, cx - radius, cy - radius, radius * 2, radius * 2);
      }
      if (peel > 0.02) {
        const depth = radius * (0.18 + peel * 0.7);
        const fold = radius - depth;
        const curl = Math.max(radius * 0.1, depth * 0.62);
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(angle);

        ctx.save();
        ctx.globalCompositeOperation = 'destination-out';
        ctx.fillStyle = '#000';
        ctx.fillRect(fold, -radius - 4, radius + 8, radius * 2 + 8);
        ctx.restore();

        const hole = ctx.createLinearGradient(fold, 0, fold + depth * 0.95, 0);
        hole.addColorStop(0, 'rgba(0,0,0,0.66)');
        hole.addColorStop(0.22, 'rgba(0,0,0,0.3)');
        hole.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = hole;
        ctx.fillRect(fold, -radius, depth + 8, radius * 2);

        ctx.save();
        ctx.beginPath();
        ctx.rect(fold - curl * 1.65, -radius, curl * 1.65, radius * 2);
        ctx.clip();
        ctx.beginPath();
        ctx.arc(fold * 2, 0, radius, 0, Math.PI * 2);
        ctx.clip();
        const cast = ctx.createLinearGradient(fold, 0, fold - curl * 1.65, 0);
        cast.addColorStop(0, 'rgba(0,0,0,0.55)');
        cast.addColorStop(0.35, 'rgba(0,0,0,0.18)');
        cast.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = cast;
        ctx.fillRect(fold - curl * 1.7, -radius, curl * 1.7, radius * 2);
        ctx.restore();

        const strips = 18;
        for (let i = 0; i < strips; i += 1) {
          const a0 = (i / strips) * Math.PI;
          const a1 = ((i + 1) / strips) * Math.PI;
          const x0 = fold - Math.sin(a0) * curl;
          const x1 = fold - Math.sin(a1) * curl;
          const left = Math.min(x0, x1);
          const span = Math.max(1.2, Math.abs(x1 - x0) + w * 0.005);
          const facing = Math.sin((a0 + a1) / 2);
          const light = 0.18 + 0.82 * Math.pow(facing, 0.65);
          const gray = Math.round(70 + light * 176);
          ctx.save();
          ctx.beginPath();
          ctx.rect(left, -radius - 2, span, radius * 2 + 4);
          ctx.clip();
          ctx.beginPath();
          ctx.arc(fold * 2, 0, radius, 0, Math.PI * 2);
          ctx.clip();
          ctx.fillStyle = `rgb(${gray},${gray},${Math.min(255, gray + 8)})`;
          ctx.fillRect(left - 1, -radius - 2, span + 2, radius * 2 + 4);
          ctx.restore();
        }

        const crest = fold - curl * 0.98;
        ctx.save();
        ctx.beginPath();
        ctx.rect(crest - curl * 0.1, -radius, curl * 0.18, radius * 2);
        ctx.clip();
        ctx.beginPath();
        ctx.arc(fold * 2, 0, radius, 0, Math.PI * 2);
        ctx.clip();
        ctx.fillStyle = 'rgba(255,255,255,0.82)';
        ctx.fillRect(crest - curl * 0.1, -radius, curl * 0.18, radius * 2);
        ctx.restore();

        const halfChord = Math.sqrt(Math.max(0, radius * radius - fold * fold));
        ctx.beginPath();
        ctx.moveTo(fold, -halfChord);
        ctx.lineTo(fold, halfChord);
        ctx.strokeStyle = 'rgba(16,16,20,0.78)';
        ctx.lineWidth = Math.max(1.4, w * 0.014);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(fold + w * 0.005, -halfChord);
        ctx.lineTo(fold + w * 0.005, halfChord);
        ctx.strokeStyle = 'rgba(255,255,255,0.62)';
        ctx.lineWidth = Math.max(1, w * 0.006);
        ctx.stroke();
        ctx.restore();
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
