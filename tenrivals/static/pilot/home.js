import React, { useEffect, useRef, useState } from 'https://esm.sh/react@18.3.1';
import { createRoot } from 'https://esm.sh/react-dom@18.3.1/client';
import htm from 'https://esm.sh/htm@3.1.1';

const html = htm.bind(React.createElement);
const data = JSON.parse(document.getElementById('home-preview-data').textContent);

function useRotator(count, ms) {
  const [index, setIndex] = useState(0);
  const [epoch, setEpoch] = useState(0);
  const indexRef = useRef(0);
  useEffect(() => {
    indexRef.current = index;
  }, [index]);
  useEffect(() => {
    if (count < 2) return undefined;
    let timer;
    const start = () => {
      clearInterval(timer);
      timer = setInterval(() => {
        setIndex((indexRef.current + 1) % count);
      }, ms);
    };
    const onVis = () => {
      if (document.hidden) clearInterval(timer);
      else start();
    };
    start();
    document.addEventListener('visibilitychange', onVis);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [count, ms, epoch]);
  const jump = (next) => {
    const n = ((next % count) + count) % count;
    setIndex(n);
    setEpoch((value) => value + 1);
  };
  return [index, jump];
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

function Hero({ hero, labels }) {
  const slides = hero.slides || [];
  const [index, jump] = useRotator(slides.length, 7000);
  return html`
    <section className="hero" id="shop-hero">
      <div className="hero-carousel" aria-hidden="true">
        ${slides.length
          ? slides.map((item, i) => html`
              <div key=${i} className=${`hero-slide${i === index ? ' is-active' : ''}`}>
                <${Linked} link=${item.link}><${Picture} image=${item.image} /></${Linked}>
              </div>
            `)
          : html`<div className="hero-slide hero-slide--placeholder is-active">${labels.heroPlaceholder}</div>`}
      </div>
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
      ${slides.length > 1 ? html`
        <button type="button" className="hero-nav hero-nav--prev" aria-label=${labels.prevHero} onClick=${() => jump(index - 1)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>
        </button>
        <button type="button" className="hero-nav hero-nav--next" aria-label=${labels.nextHero} onClick=${() => jump(index + 1)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
        </button>
      ` : null}
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

function Rail({ controlsLabel, prevLabel, nextLabel, tone, children }) {
  const track = useRef(null);
  const items = React.Children.toArray(children);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(0);
  const widthOf = () => {
    const el = track.current;
    const first = el && el.firstElementChild;
    return (first && first.getBoundingClientRect().width) || 1;
  };
  useEffect(() => {
    const el = track.current;
    if (!el) return undefined;
    const measure = () => {
      const visible = Math.max(1, Math.floor(el.clientWidth / widthOf()));
      setPages(Math.max(1, el.children.length - visible + 1));
    };
    const onScroll = () => setPage(Math.round(el.scrollLeft / widthOf()));
    measure();
    const obs = new ResizeObserver(measure);
    obs.observe(el);
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      obs.disconnect();
      el.removeEventListener('scroll', onScroll);
    };
  }, [items.length]);
  const scrollByDir = (dir) => {
    const el = track.current;
    if (!el) return;
    el.scrollBy({ left: dir * widthOf(), behavior: 'smooth' });
  };
  const go = (i) => {
    const el = track.current;
    if (!el) return;
    el.scrollTo({ left: i * widthOf(), behavior: 'smooth' });
  };
  return html`
    <div className="product-rail-wrap">
      <div className="product-rail">
        <div className="product-rail__track" ref=${track}>${items}</div>
      </div>
      <div className=${`carousel-controls carousel-controls--${tone}`} aria-label=${controlsLabel}>
        <button type="button" className="carousel-controls__arrow" aria-label=${prevLabel} onClick=${() => scrollByDir(-1)}>←</button>
        <div className="carousel-controls__dots">
          ${Array.from({ length: pages }, (_, i) => html`
            <button
              key=${i}
              type="button"
              className=${`carousel-controls__dot${i === page ? ' is-active' : ''}`}
              aria-label=${`Go to position ${i + 1}`}
              onClick=${() => go(i)}
            ></button>
          `)}
        </div>
        <button type="button" className="carousel-controls__arrow" aria-label=${nextLabel} onClick=${() => scrollByDir(1)}>→</button>
      </div>
    </div>
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
  const [index, jump] = useRotator(slides.length, 7000);
  if (!slides.length) return null;
  return html`
    <section className="section promo-band">
      <div className="promo-carousel">
        <div className="promo-carousel__viewport">
          ${slides.map((item, i) => html`
            <div key=${i} className=${`promo-carousel__slide${i === index ? ' is-active' : ''}`}>
              <${Linked} link=${item.link}><${Picture} image=${item.image} /></${Linked}>
            </div>
          `)}
        </div>
        ${slides.length > 1 ? html`
          <div className="carousel-controls carousel-controls--navy promo-carousel__controls" aria-label=${promos.controls}>
            <button type="button" className="carousel-controls__arrow" aria-label=${promos.prev} onClick=${() => jump(index - 1)}>←</button>
            <div className="carousel-controls__dots" aria-label=${promos.controls}>
              ${slides.map((_, i) => html`
                <button
                  key=${i}
                  type="button"
                  className=${`carousel-controls__dot${i === index ? ' is-active' : ''}`}
                  aria-label=${`${promos.goTo} ${i + 1}`}
                  onClick=${() => jump(i)}
                ></button>
              `)}
            </div>
            <button type="button" className="carousel-controls__arrow" aria-label=${promos.next} onClick=${() => jump(index + 1)}>→</button>
          </div>
        ` : null}
      </div>
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

function Brands({ brands }) {
  const row = (hidden) => brands.items.map((item) => html`
    <li key=${`${hidden ? 'b' : 'a'}-${item.href}`} aria-hidden=${hidden ? 'true' : undefined}>
      <a
        className="brand-logo-link"
        style=${{ '--brand-optical-scale': item.scale }}
        href=${item.href}
        title=${hidden ? undefined : item.title}
        tabIndex=${hidden ? -1 : undefined}
      >
        <img src=${item.src} alt=${hidden ? '' : item.alt} width="280" height="64" loading="lazy" decoding="async" />
      </a>
    </li>
  `);
  return html`
    <section className="brands-strip">
      <div className="container">
        <${SectionHead} title=${brands.title} href=${brands.viewAll} viewAll=${data.labels.viewAll} />
        <div className="brands-marquee" aria-label=${brands.label}>
          <div className="brands-marquee__track">
            <ul className="brands-marquee__sequence">
              ${row(false)}
              ${row(true)}
            </ul>
          </div>
        </div>
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
