"use strict";

// Включает плавные появления блоков при прокрутке и тень шапки.
// Если JavaScript выключен или в системе отключена анимация, всё просто видно сразу.
document.documentElement.classList.add("js");

(function () {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function updateHeader() {
    document.documentElement.classList.toggle("scrolled", window.scrollY > 8);
  }
  window.addEventListener("scroll", updateHeader, { passive: true });
  updateHeader();

  document.addEventListener("DOMContentLoaded", function () {
    const targets = document.querySelectorAll(".reveal, .stagger");
    if (reduce || !("IntersectionObserver" in window)) {
      targets.forEach((el) => el.classList.add("is-visible"));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");  // один раз: назад не прячем
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0, rootMargin: "0px 0px -8% 0px" }
    );
    targets.forEach((el) => observer.observe(el));
  });
})();
