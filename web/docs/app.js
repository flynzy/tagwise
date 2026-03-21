/* =============================================
   TAGWISE DOCS — App Logic
   Hash-based routing, sidebar, search, TOC
   ============================================= */

(function () {
  'use strict';

  // ===== PAGE REGISTRY =====
  // Defines all pages, their tab, sidebar section, and display info
  const pages = [
    // Welcome tab
    { id: 'overview', tab: 'welcome', section: 'Introduction', title: 'Overview', icon: '📖' },
    { id: 'security', tab: 'welcome', section: 'Introduction', title: 'Security', icon: '🔒' },
    // Getting Started tab
    { id: 'setting-up', tab: 'getting-started', section: 'Basics', title: 'Setting Up', icon: '🚀' },
    { id: 'first-trade', tab: 'getting-started', section: 'Basics', title: 'Your First Trade', icon: '💰' },
    // Features tab
    { id: 'wallet-tracking', tab: 'features', section: 'Core', title: 'Wallet Tracking', icon: '📡' },
    { id: 'leaderboard', tab: 'features', section: 'Core', title: 'Leaderboard', icon: '🏆' },
    { id: 'copy-trading', tab: 'features', section: 'Core', title: 'Copy Trading', icon: '⚡' },
    { id: 'wallet-analytics', tab: 'features', section: 'Core', title: 'Wallet Analytics', icon: '📊' },
    { id: 'multi-buy-alerts', tab: 'features', section: 'Advanced', title: 'Multi-Buy Alerts', icon: '🔔', badge: 'PRO' },
    { id: 'confidence-scoring', tab: 'features', section: 'Advanced', title: 'Confidence Scoring', icon: '🎯', badge: 'PRO' },
    { id: 'referral-system', tab: 'features', section: 'Growth', title: 'Referral System', icon: '🤝' },
    { id: 'command-reference', tab: 'features', section: 'Reference', title: 'Command Reference', icon: '⌨️' },
    // Pricing tab
    { id: 'free-vs-pro', tab: 'pricing', section: 'Plans', title: 'Free vs. PRO', icon: '💎' },
    { id: 'payment', tab: 'pricing', section: 'Plans', title: 'Payment', icon: '💳' },
  ];

  // Default page per tab
  const tabDefaults = {
    'welcome': 'overview',
    'getting-started': 'setting-up',
    'features': 'wallet-tracking',
    'pricing': 'free-vs-pro'
  };

  // ===== DOM REFS =====
  const topTabs = document.getElementById('topTabs');
  const mobileTabs = document.getElementById('mobileTabs');
  const sidebarContent = document.getElementById('sidebarContent');
  const sidebar = document.getElementById('sidebar');
  const sidebarOverlay = document.getElementById('sidebarOverlay');
  const hamburger = document.getElementById('hamburger');
  const tocEl = document.getElementById('toc');
  const tocLinks = document.getElementById('tocLinks');
  const searchOverlay = document.getElementById('searchOverlay');
  const searchTrigger = document.getElementById('searchTrigger');
  const searchInput = document.getElementById('searchInput');
  const searchResults = document.getElementById('searchResults');

  let currentPage = null;
  let currentTab = null;
  let searchHighlightIdx = -1;

  // ===== SIDEBAR RENDERING =====
  function renderSidebar(tab) {
    const tabPages = pages.filter(p => p.tab === tab);
    const sections = {};
    tabPages.forEach(p => {
      if (!sections[p.section]) sections[p.section] = [];
      sections[p.section].push(p);
    });

    let html = '';
    for (const [sectionName, sectionPages] of Object.entries(sections)) {
      html += `<div class="sidebar-section">`;
      html += `<div class="sidebar-section-title">${sectionName}</div>`;
      sectionPages.forEach(p => {
        const activeClass = p.id === currentPage ? ' active' : '';
        const badge = p.badge ? `<span class="badge">${p.badge}</span>` : '';
        html += `<a class="sidebar-link${activeClass}" data-page="${p.id}">${p.title}${badge}</a>`;
      });
      html += `</div>`;
    }
    sidebarContent.innerHTML = html;

    // Bind clicks
    sidebarContent.querySelectorAll('.sidebar-link').forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        navigateTo(link.dataset.page);
        closeMobileSidebar();
      });
    });
  }

  // ===== TAB SWITCHING =====
  function setActiveTab(tab) {
    currentTab = tab;
    // Desktop tabs
    topTabs.querySelectorAll('.top-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.tab === tab);
    });
    // Mobile tabs
    mobileTabs.querySelectorAll('.mobile-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.tab === tab);
    });
    renderSidebar(tab);
  }

  // ===== PAGE DISPLAY =====
  function showPage(pageId) {
    const page = pages.find(p => p.id === pageId);
    if (!page) return;

    // Set tab if different
    if (currentTab !== page.tab) {
      setActiveTab(page.tab);
    }

    // Hide all pages, show target
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const pageEl = document.getElementById('page-' + pageId);
    if (pageEl) {
      pageEl.classList.add('active');
      // Add footer to page if not present
      ensureFooter(pageEl);
      // Add page nav (prev/next)
      ensurePageNav(pageEl, pageId);
    }

    currentPage = pageId;

    // Update sidebar active state
    sidebarContent.querySelectorAll('.sidebar-link').forEach(link => {
      link.classList.toggle('active', link.dataset.page === pageId);
    });

    // Generate TOC
    generateTOC(pageEl);

    // Scroll to top
    window.scrollTo(0, 0);
  }

  // ===== FOOTER =====
  function ensureFooter(pageEl) {
    if (pageEl.querySelector('.footer')) return;
    const content = pageEl.querySelector('.content');
    if (!content) return;
    // Remove existing footer if any
    const existing = content.querySelector('.footer');
    if (existing) existing.remove();

    const footer = document.createElement('div');
    footer.className = 'footer';
    footer.innerHTML = `
      <span>Powered by <a href="https://tagwise.xyz" target="_blank" rel="noopener noreferrer">Tagwise</a></span>
      <span>© ${new Date().getFullYear()} Tagwise</span>
    `;
    content.appendChild(footer);
  }

  // ===== PAGE NAV (Prev / Next) =====
  function ensurePageNav(pageEl, pageId) {
    const content = pageEl.querySelector('.content');
    if (!content) return;
    // Remove existing nav
    const existing = content.querySelector('.page-nav');
    if (existing) existing.remove();

    const idx = pages.findIndex(p => p.id === pageId);
    const prev = idx > 0 ? pages[idx - 1] : null;
    const next = idx < pages.length - 1 ? pages[idx + 1] : null;

    if (!prev && !next) return;

    const nav = document.createElement('div');
    nav.className = 'page-nav';

    if (prev) {
      nav.innerHTML += `
        <div class="page-nav-link prev" data-page="${prev.id}">
          <div class="page-nav-label">← Previous</div>
          <div class="page-nav-title">${prev.title}</div>
        </div>`;
    } else {
      nav.innerHTML += `<div></div>`;
    }

    if (next) {
      nav.innerHTML += `
        <div class="page-nav-link next" data-page="${next.id}">
          <div class="page-nav-label">Next →</div>
          <div class="page-nav-title">${next.title}</div>
        </div>`;
    }

    // Insert before footer
    const footer = content.querySelector('.footer');
    if (footer) {
      content.insertBefore(nav, footer);
    } else {
      content.appendChild(nav);
    }

    nav.querySelectorAll('.page-nav-link').forEach(link => {
      link.addEventListener('click', () => navigateTo(link.dataset.page));
    });
  }

  // ===== TABLE OF CONTENTS =====
  function generateTOC(pageEl) {
    if (!pageEl) {
      tocLinks.innerHTML = '';
      return;
    }

    const headings = pageEl.querySelectorAll('.content h2, .content h3');
    let html = '';
    headings.forEach(h => {
      if (!h.id) return;
      const level = h.tagName === 'H3' ? ' toc-h3' : '';
      html += `<a class="toc-link${level}" data-target="${h.id}">${h.textContent}</a>`;
    });
    tocLinks.innerHTML = html;

    tocLinks.querySelectorAll('.toc-link').forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const target = document.getElementById(link.dataset.target);
        if (target) {
          target.scrollIntoView({ behavior: 'smooth' });
        }
      });
    });

    // Observe headings for active state
    observeHeadings(pageEl);
  }

  // ===== HEADING OBSERVER (for TOC active state) =====
  let headingObserver = null;
  function observeHeadings(pageEl) {
    if (headingObserver) headingObserver.disconnect();

    const headings = pageEl.querySelectorAll('.content h2[id], .content h3[id]');
    if (!headings.length) return;

    headingObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          tocLinks.querySelectorAll('.toc-link').forEach(l => l.classList.remove('active'));
          const active = tocLinks.querySelector(`[data-target="${entry.target.id}"]`);
          if (active) active.classList.add('active');
        }
      });
    }, {
      rootMargin: '-80px 0px -70% 0px',
      threshold: 0
    });

    headings.forEach(h => headingObserver.observe(h));
  }

  // ===== NAVIGATION =====
  window.navigateTo = function (pageId) {
    window.location.hash = pageId;
  };

  function handleHash() {
    let hash = window.location.hash.replace('#', '');
    // If hash is a tab name, go to its default page
    if (tabDefaults[hash]) {
      hash = tabDefaults[hash];
      window.location.hash = hash;
      return;
    }
    // If no hash or invalid, go to overview
    const page = pages.find(p => p.id === hash);
    if (!page) {
      window.location.hash = 'overview';
      return;
    }
    showPage(hash);
  }

  // ===== MOBILE SIDEBAR =====
  function toggleMobileSidebar() {
    const isOpen = sidebar.classList.contains('open');
    if (isOpen) {
      closeMobileSidebar();
    } else {
      sidebar.classList.add('open');
      sidebarOverlay.classList.add('active');
      hamburger.classList.add('active');
    }
  }

  function closeMobileSidebar() {
    sidebar.classList.remove('open');
    sidebarOverlay.classList.remove('active');
    hamburger.classList.remove('active');
  }

  hamburger.addEventListener('click', toggleMobileSidebar);
  sidebarOverlay.addEventListener('click', closeMobileSidebar);

  // ===== SEARCH =====
  function openSearch() {
    searchOverlay.classList.add('open');
    searchInput.value = '';
    searchInput.focus();
    renderSearchResults('');
    searchHighlightIdx = -1;
  }

  function closeSearch() {
    searchOverlay.classList.remove('open');
    searchInput.value = '';
    searchHighlightIdx = -1;
  }

  function renderSearchResults(query) {
    const q = query.toLowerCase().trim();
    let results = pages;
    if (q) {
      results = pages.filter(p =>
        p.title.toLowerCase().includes(q) ||
        p.section.toLowerCase().includes(q) ||
        p.tab.toLowerCase().includes(q) ||
        p.id.toLowerCase().includes(q)
      );
    }

    if (results.length === 0) {
      searchResults.innerHTML = `<div class="search-empty">No results for "${query}"</div>`;
      return;
    }

    searchResults.innerHTML = results.map((p, i) => `
      <div class="search-result${i === searchHighlightIdx ? ' highlighted' : ''}" data-page="${p.id}" data-index="${i}">
        <div class="search-result-icon">${p.icon}</div>
        <div class="search-result-text">
          <div class="search-result-title">${highlightMatch(p.title, q)}</div>
          <div class="search-result-section">${capitalize(p.tab)} → ${p.section}</div>
        </div>
      </div>
    `).join('');

    searchResults.querySelectorAll('.search-result').forEach(r => {
      r.addEventListener('click', () => {
        navigateTo(r.dataset.page);
        closeSearch();
      });
    });
  }

  function highlightMatch(text, query) {
    if (!query) return text;
    const idx = text.toLowerCase().indexOf(query);
    if (idx === -1) return text;
    return text.slice(0, idx) + '<strong style="color:var(--accent)">' + text.slice(idx, idx + query.length) + '</strong>' + text.slice(idx + query.length);
  }

  function capitalize(str) {
    return str.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  }

  searchTrigger.addEventListener('click', openSearch);

  searchOverlay.addEventListener('click', (e) => {
    if (e.target === searchOverlay) closeSearch();
  });

  searchInput.addEventListener('input', () => {
    searchHighlightIdx = -1;
    renderSearchResults(searchInput.value);
  });

  // Keyboard navigation in search
  searchInput.addEventListener('keydown', (e) => {
    const items = searchResults.querySelectorAll('.search-result');
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      searchHighlightIdx = Math.min(searchHighlightIdx + 1, items.length - 1);
      updateSearchHighlight(items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      searchHighlightIdx = Math.max(searchHighlightIdx - 1, 0);
      updateSearchHighlight(items);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const highlighted = searchResults.querySelector('.search-result.highlighted');
      if (highlighted) {
        navigateTo(highlighted.dataset.page);
        closeSearch();
      } else if (items.length > 0) {
        navigateTo(items[0].dataset.page);
        closeSearch();
      }
    }
  });

  function updateSearchHighlight(items) {
    items.forEach((item, i) => {
      item.classList.toggle('highlighted', i === searchHighlightIdx);
    });
    const highlighted = searchResults.querySelector('.search-result.highlighted');
    if (highlighted) highlighted.scrollIntoView({ block: 'nearest' });
  }

  // Global keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    // Cmd+K or Ctrl+K or /
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      openSearch();
    } else if (e.key === '/' && !e.metaKey && !e.ctrlKey) {
      // Only if not typing in an input
      if (document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
        e.preventDefault();
        openSearch();
      }
    } else if (e.key === 'Escape') {
      if (searchOverlay.classList.contains('open')) {
        closeSearch();
      }
    }
  });

  // ===== TAB CLICK HANDLERS =====
  topTabs.querySelectorAll('.top-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const tabName = tab.dataset.tab;
      const defaultPage = tabDefaults[tabName];
      navigateTo(defaultPage);
    });
  });

  mobileTabs.querySelectorAll('.mobile-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const tabName = tab.dataset.tab;
      const defaultPage = tabDefaults[tabName];
      setActiveTab(tabName);
      renderSidebar(tabName);
      navigateTo(defaultPage);
    });
  });

  // ===== INIT =====
  // Mark all pages as JS-loaded to disable CSS-only fallback
  document.querySelectorAll('.page').forEach(p => p.classList.add('js-loaded'));

  window.addEventListener('hashchange', handleHash);

  // Initial load
  handleHash();
  if (!window.location.hash || window.location.hash === '#') {
    window.location.hash = 'overview';
  }

})();
