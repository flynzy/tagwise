/* ============================
   TAGWISE — Landing Page Scripts
   ============================ */

(function () {
    'use strict';

    // --- Scroll Reveal (Intersection Observer) ---
    const revealElements = document.querySelectorAll('.reveal');
    
    const revealObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                revealObserver.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.1,
        rootMargin: '0px 0px -40px 0px'
    });

    revealElements.forEach(el => revealObserver.observe(el));

    // --- Navigation scroll state ---
    const nav = document.getElementById('nav');
    let lastScroll = 0;

    function updateNav() {
        const scrollY = window.scrollY;
        if (scrollY > 100) {
            nav.classList.add('scrolled');
        } else {
            nav.classList.remove('scrolled');
        }
        lastScroll = scrollY;
    }

    window.addEventListener('scroll', updateNav, { passive: true });
    updateNav();

    // --- Mobile Menu ---
    const mobileMenuBtn = document.getElementById('mobileMenuBtn');
    const mobileMenu = document.getElementById('mobileMenu');

    if (mobileMenuBtn && mobileMenu) {
        mobileMenuBtn.addEventListener('click', () => {
            mobileMenuBtn.classList.toggle('active');
            mobileMenu.classList.toggle('active');
        });

        // Close mobile menu on link click
        mobileMenu.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => {
                mobileMenuBtn.classList.remove('active');
                mobileMenu.classList.remove('active');
            });
        });
    }

    // --- Smooth Scroll for anchor links ---
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            const href = this.getAttribute('href');
            if (href === '#') return; // skip placeholder links
            e.preventDefault();
            const target = document.querySelector(href);
            if (target) {
                const offset = 80;
                const top = target.getBoundingClientRect().top + window.scrollY - offset;
                window.scrollTo({ top, behavior: 'smooth' });
            }
        });
    });

    // --- Leaderboard Tab Interactivity ---
    const lbTabs = document.querySelectorAll('.lb-tab');
    const lbTimeTabs = document.querySelectorAll('.lb-time');

    lbTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            lbTabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
        });
    });

    lbTimeTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            lbTimeTabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
        });
    });

    // --- Typing / Stagger animation for Telegram messages ---
    const tgMessages = document.querySelectorAll('.tg-message');
    let messageDelay = 0;
    
    const tgObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                tgMessages.forEach((msg, i) => {
                    msg.style.opacity = '0';
                    msg.style.transform = 'translateY(16px)';
                    msg.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
                    msg.style.transitionDelay = `${i * 0.3}s`;
                    
                    requestAnimationFrame(() => {
                        msg.style.opacity = '1';
                        msg.style.transform = 'translateY(0)';
                    });
                });
                tgObserver.unobserve(entry.target);
            }
        });
    }, { threshold: 0.3 });

    const tgChat = document.querySelector('.tg-chat');
    if (tgChat) {
        tgObserver.observe(tgChat);
    }

    // --- Parallax on floating cards ---
    const floatingCards = document.querySelectorAll('.floating-card');
    
    if (window.matchMedia('(min-width: 769px)').matches) {
        window.addEventListener('mousemove', (e) => {
            const x = (e.clientX / window.innerWidth - 0.5) * 2;
            const y = (e.clientY / window.innerHeight - 0.5) * 2;
            
            floatingCards.forEach((card, i) => {
                const factor = (i + 1) * 5;
                card.style.transform = `translate(${x * factor}px, ${y * factor}px)`;
            });
        }, { passive: true });
    }

    // --- Counter animation for leaderboard PnL ---
    function animateValue(element, start, end, duration, prefix, suffix) {
        const startTime = performance.now();
        
        function update(currentTime) {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            const current = Math.floor(start + (end - start) * eased);
            element.textContent = prefix + current.toLocaleString() + suffix;
            
            if (progress < 1) {
                requestAnimationFrame(update);
            }
        }
        
        requestAnimationFrame(update);
    }

    const lbPnlElements = document.querySelectorAll('.lb-positive');
    let lbAnimated = false;

    const lbObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting && !lbAnimated) {
                lbAnimated = true;
                lbPnlElements.forEach(el => {
                    const text = el.textContent;
                    if (text.startsWith('+$')) {
                        const value = parseInt(text.replace(/[^0-9]/g, ''));
                        animateValue(el, 0, value, 1500, '+$', '');
                    } else if (text.startsWith('+')) {
                        const value = parseInt(text.replace(/[^0-9]/g, ''));
                        animateValue(el, 0, value, 1500, '+', '%');
                    }
                });
            }
        });
    }, { threshold: 0.3 });

    const lbTable = document.querySelector('.lb-table');
    if (lbTable) {
        lbObserver.observe(lbTable);
    }

})();

(() => {
    const NEWSLETTER_ENDPOINT = ""; // e.g. "https://your-api.com/subscribe" (optional)
    const NEWSLETTER_MAILTO_TO = "tagwisebot@gmail.com"; // change if needed
  
    function isValidEmail(email) {
      return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(email || "").trim());
    }
  
    async function submitNewsletter(email) {
      const clean = String(email || "").trim();
  
      if (!isValidEmail(clean)) {
        alert("Please enter a valid email.");
        return;
      }
  
      if (NEWSLETTER_ENDPOINT) {
        const res = await fetch(NEWSLETTER_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: clean, source: "tagwise-landing" }),
        });
  
        if (!res.ok) {
          alert("Subscription failed. Please try again.");
          return;
        }
  
        alert("Subscribed — thanks!");
        return;
      }
  
      const subject = encodeURIComponent("Tagwise — keep me in touch");
      const body = encodeURIComponent(`Please add me to the Tagwise updates list:\n\n${clean}\n`);
      window.location.href = `mailto:${NEWSLETTER_MAILTO_TO}?subject=${subject}&body=${body}`;
    }
  
    document.addEventListener("DOMContentLoaded", () => {
      const input = document.querySelector(".newsletter-input");
      const btn = document.querySelector(".newsletter-btn");
  
      if (!input || !btn) return;
  
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        submitNewsletter(input.value);
      });
  
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          submitNewsletter(input.value);
        }
      });
    });
  })();
  