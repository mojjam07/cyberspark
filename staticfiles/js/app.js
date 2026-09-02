document.addEventListener("DOMContentLoaded", function () {
  // Add transition-ready class after DOM loads (small delay for smooth effect)
  setTimeout(() => {
    document.body.classList.add('transition-ready');
  }, 50);
  
  // Cache DOM elements ONCE
  const hamburger = document.getElementById("hamburger-btn");
  const mainNav = document.getElementById("main-nav");

  // Mobile Nav Toggle
  if (hamburger && mainNav) {
    hamburger.addEventListener("click", () => {
      const isOpen = mainNav.classList.toggle("mobile-open");
      hamburger.classList.toggle("open", isOpen);
      hamburger.setAttribute("aria-expanded", String(isOpen));
    });
  }

  // Close mobile nav + trigger smooth transition on link click
  document.querySelectorAll('.nav-link, .nav-cta, .brand').forEach(link => {
    link.addEventListener('click', (e) => {
      // Close mobile menu
      mainNav.classList.remove('mobile-open');
      if (hamburger) {
        hamburger.classList.remove('open');
        hamburger.setAttribute('aria-expanded', 'false');
      }
      
      // Fade out before navigation
      document.body.classList.remove('transition-ready');
    });
  });

  // Active nav (robust version)
  const currentPath = window.location.pathname;
  document.querySelectorAll(".nav-link").forEach(link => {
    const linkPath = new URL(link.href).pathname;
    if (linkPath === currentPath) {
      link.classList.add("active");
    }
  });

  // Account dropdown (desktop nav) — click to toggle, click outside or
  // Escape to close. On mobile this trigger is inert (see CSS) and the
  // panel is always shown flat, so this code simply has nothing to do.
  const navAccount = document.getElementById("navAccount");
  const navAccountTrigger = document.getElementById("navAccountTrigger");
  if (navAccount && navAccountTrigger) {
    function closeAccountMenu() {
      navAccount.classList.remove("open");
      navAccountTrigger.setAttribute("aria-expanded", "false");
    }
    navAccountTrigger.addEventListener("click", (e) => {
      e.stopPropagation();
      const isOpen = navAccount.classList.toggle("open");
      navAccountTrigger.setAttribute("aria-expanded", String(isOpen));
    });
    document.addEventListener("click", (e) => {
      if (!navAccount.contains(e.target)) closeAccountMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeAccountMenu();
    });
  }

  // Toast notifications: auto-dismiss + manual close
  document.querySelectorAll('.toast').forEach(function (toast) {
    function dismiss() {
      if (toast.classList.contains('toast-hide')) return;
      toast.classList.add('toast-hide');
      setTimeout(() => toast.remove(), 200);
    }
    const closeBtn = toast.querySelector('.toast-close');
    if (closeBtn) closeBtn.addEventListener('click', dismiss);
    setTimeout(dismiss, 5000);
  });
});

