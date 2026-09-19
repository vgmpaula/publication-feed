    (() => {
      'use strict';

    // Only the feed page is modified; the homepage remains in its own repository.
    const menuToggle = document.querySelector('.menu-toggle');
    const siteNav = document.getElementById('site-nav');
    const backToTop = document.getElementById('back-to-top');
    const year = document.getElementById('year');
    if (year) year.textContent = String(new Date().getFullYear());
    const closeMenu = () => {
      if (!menuToggle || !siteNav) return;
      siteNav.classList.remove('is-open');
      menuToggle.setAttribute('aria-expanded', 'false');
      menuToggle.setAttribute('aria-label', 'Open navigation');
    };
    if (menuToggle && siteNav) {
      menuToggle.addEventListener('click', () => {
        const opening = menuToggle.getAttribute('aria-expanded') !== 'true';
        siteNav.classList.toggle('is-open', opening);
        menuToggle.setAttribute('aria-expanded', String(opening));
        menuToggle.setAttribute('aria-label', opening ? 'Close navigation' : 'Open navigation');
      });
      siteNav.querySelectorAll('a').forEach(link => link.addEventListener('click', closeMenu));
      document.addEventListener('keydown', event => { if (event.key === 'Escape') closeMenu(); });
      document.addEventListener('click', event => {
        if (!siteNav.contains(event.target) && !menuToggle.contains(event.target)) closeMenu();
      });
    }
    if (backToTop) {
      const update = () => backToTop.classList.toggle('visible', window.scrollY > 450);
      window.addEventListener('scroll', update, { passive: true });
      backToTop.addEventListener('click', () => window.scrollTo({
        top: 0,
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
      }));
      update();
    }

      const categories = [
        ['articles', 'Articles'],
        ['conference-abstracts', 'Conference proceedings'],
        ['oral-communications', 'Oral communications'],
        ['posters', 'Posters'],
        ['dissemination', 'Dissemination'],
        ['theses', 'Theses']
      ];
      const holder = document.getElementById('pub-records');
      const status = document.getElementById('pub-feed-status');

      // Text-only DOM creation: ORCID metadata is never inserted as raw HTML.
      const element = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
      };
      const safeUrl = (work) => {
        if (typeof work.doi === 'string' && work.doi.trim()) {
          const doi = work.doi.trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '').replace(/^doi:\s*/i, '');
          return `https://doi.org/${encodeURI(doi)}`;
        }
        if (typeof work.url === 'string') {
          try {
            const url = new URL(work.url);
            if (url.protocol === 'https:' || url.protocol === 'http:') return url.href;
          } catch (_) { /* Ignore invalid external URLs. */ }
        }
        return null;
      };

      const renderRecord = (work) => {
        const article = element('article', 'pub-record');
        article.append(element('div', 'pub-record-year', work.year || '—'));
        const body = element('div', 'pub-record-body');
        const title = element('h3', 'pub-record-title');
        const url = safeUrl(work);
        const titleNode = element(url ? 'a' : 'span', '', work.title || 'Untitled work');
        if (url) {
          titleNode.href = url;
          titleNode.target = '_blank';
          titleNode.rel = 'noopener noreferrer';
        }
        title.append(titleNode);
        body.append(title);
        if (Array.isArray(work.authors) && work.authors.length) {
          body.append(element('p', 'pub-record-authors', work.authors.join(', ')));
        }
        const metadata = element('p', 'pub-record-meta');
        if (work.venue) metadata.append(element('span', 'pub-venue', work.venue));
        if (work.doi) {
          if (metadata.childNodes.length) metadata.append(document.createTextNode(' · '));
          const doiNode = element('a', '', `DOI: ${work.doi}`);
          doiNode.href = `https://doi.org/${encodeURI(String(work.doi).replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '').replace(/^doi:\s*/i, ''))}`;
          doiNode.target = '_blank';
          doiNode.rel = 'noopener noreferrer';
          metadata.append(doiNode);
        }
        if (metadata.childNodes.length) body.append(metadata);
        article.append(body);
        return article;
      };

      const renderCategory = (key, label, works) => {
        if (!works.length) return;
        const section = element('section', 'pub-section');
        section.id = key;
        const heading = element('div', 'pub-section-heading');
        const title = element('h2');
        title.append(document.createTextNode(label + ' '), element('em', '', `(${works.length})`));
        heading.append(title);
        section.append(heading);
        const list = element('div', 'pub-records');
        works.forEach((work) => list.append(renderRecord(work)));
        section.append(list);
        holder.append(section);
      };

      const load = async () => {
        const response = await fetch('./publications.json', { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const feed = await response.json();
        if (!feed || !Array.isArray(feed.works)) throw new Error('Unexpected publication data');
        const works = feed.works;
        const known = new Set(categories.map(([key]) => key));
        const sections = new Map(categories.map(([key]) => [key, []]));
        const others = [];
        for (const work of works) {
          const key = work?.category || 'other';
          if (sections.has(key)) sections.get(key).push(work);
          else others.push(work);
        }
        holder.replaceChildren();
        categories.forEach(([key, label], i) => {
          const entries = sections.get(key);
          document.querySelector(`[data-category-count="${key}"]`).textContent = String(entries.length);
          const statLink = document.querySelector(`.pub-stat[href="#${key}"]`);
          if (statLink && !entries.length) {
            statLink.removeAttribute('href');
            statLink.setAttribute('aria-disabled', 'true');
          }
          renderCategory(key, label, entries);
        });
        if (others.length) renderCategory('other', 'Other outputs', others);
        if (!works.length) holder.append(element('p', 'pub-empty', 'No public research outputs are currently listed on ORCID.'));
        const updated = feed.last_updated_utc ? new Date(feed.last_updated_utc) : null;
        const date = updated && !Number.isNaN(updated.getTime()) ? ` · Updated ${updated.toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric'})}` : '';
        status.textContent = `${works.length} public ORCID records${date}`;
      };
      load().catch(() => {
        holder.replaceChildren();
        const error = element('p', 'pub-error');
        error.append(document.createTextNode('The publication feed is temporarily unavailable. You can still '));
        const link = element('a', '', 'read my ORCID record');
        link.href = 'https://orcid.org/0000-0002-8255-9494';
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        error.append(link, document.createTextNode('.'));
        holder.append(error);
        status.textContent = 'Publication feed temporarily unavailable';
      });
    })();
