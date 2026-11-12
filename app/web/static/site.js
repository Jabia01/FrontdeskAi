/* Chichi — public site behaviour.
   Vanilla, no dependencies, no build step. Four things:
   the sticky nav, scroll reveals, the phone demo replay, and the language cycle.

   Everything checks prefers-reduced-motion first: with motion reduced, the
   content is all still there, it simply arrives at once instead of in sequence.
*/
(function () {
  "use strict";

  var still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ------------------------------------------------------------- the nav */
  var nav = document.querySelector(".nav");
  if (nav) {
    var onScroll = function () {
      nav.classList.toggle("stuck", window.scrollY > 12);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* --------------------------------------------------------- scroll reveals */
  var reveals = document.querySelectorAll(".rise, .timeline");
  if (still || !("IntersectionObserver" in window)) {
    reveals.forEach(function (el) { el.classList.add("in"); });
  } else {
    var seer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("in");
          seer.unobserve(entry.target);
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
    );
    reveals.forEach(function (el) { seer.observe(el); });
  }

  /* ------------------------------------------------------- the phone demo */
  var thread = document.querySelector("[data-thread]");
  if (thread) {
    var script = [];
    try {
      script = JSON.parse(
        document.getElementById("demo-script").textContent
      );
    } catch (e) {
      script = [];
    }

    var bubble = function (turn) {
      var el = document.createElement("div");
      el.className =
        "bubble " + (turn.from === "customer" ? "customer" : "chichi") +
        (turn.booked ? " booked" : "");
      el.textContent = turn.text;
      if (turn.stamp) {
        var stamp = document.createElement("span");
        stamp.className = "stamp";
        stamp.textContent = turn.stamp;
        el.appendChild(stamp);
      }
      return el;
    };

    var typing = function () {
      var el = document.createElement("div");
      el.className = "typing";
      el.innerHTML = "<i></i><i></i><i></i>";
      return el;
    };

    /* Keep the thread from outgrowing the screen: once it is taller than the
       frame, drop the oldest bubble as each new one lands. */
    var trim = function () {
      while (thread.scrollHeight > thread.clientHeight && thread.children.length > 1) {
        thread.removeChild(thread.firstElementChild);
      }
    };

    var wait = function (ms) {
      return new Promise(function (resolve) { setTimeout(resolve, ms); });
    };

    var play = function () {
      if (still) {
        /* No animation: show the whole exchange at once. */
        script.forEach(function (turn) {
          var el = bubble(turn);
          el.style.animation = "none";
          el.style.opacity = 1;
          el.style.transform = "none";
          thread.appendChild(el);
        });
        trim();
        return;
      }

      var step = function (index) {
        if (index >= script.length) {
          /* Hold on the finished conversation, then run it again. */
          return wait(6500).then(function () {
            thread.innerHTML = "";
            return step(0);
          });
        }
        var turn = script[index];
        return wait(turn.delay || 500)
          .then(function () {
            if (turn.from === "customer") return null;
            var dots = typing();
            thread.appendChild(dots);
            trim();
            return wait(turn.typing || 1200).then(function () {
              thread.removeChild(dots);
            });
          })
          .then(function () {
            thread.appendChild(bubble(turn));
            trim();
            return step(index + 1);
          });
      };

      step(0);
    };

    /* Only start once it is actually on screen - nobody should be paying for
       a battery to animate a phone they cannot see. */
    if ("IntersectionObserver" in window) {
      var watcher = new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting) {
          watcher.disconnect();
          play();
        }
      }, { threshold: 0.3 });
      watcher.observe(thread);
    } else {
      play();
    }
  }

  /* ---------------------------------------------------- the language cycle */
  var stage = document.querySelector("[data-languages]");
  if (stage) {
    var langs = [];
    try {
      langs = JSON.parse(document.getElementById("language-demo").textContent);
    } catch (e) {
      langs = [];
    }
    var out = stage.querySelector(".lang-in");
    var chips = Array.prototype.slice.call(stage.querySelectorAll(".lang-chip"));
    var at = 0;

    var show = function (index) {
      chips.forEach(function (chip, i) { chip.classList.toggle("on", i === index); });
      out.querySelector("span").textContent = langs[index].text;
      out.setAttribute("lang", langs[index].code);
      out.setAttribute("dir", langs[index].code === "ar" ? "rtl" : "ltr");
    };

    if (langs.length) {
      show(0);
      if (!still) {
        setInterval(function () {
          out.classList.add("swap");
          setTimeout(function () {
            at = (at + 1) % langs.length;
            show(at);
            out.classList.remove("swap");
          }, 320);
        }, 2600);
      }
      chips.forEach(function (chip, i) {
        chip.addEventListener("click", function () { at = i; show(i); });
      });
    }
  }

  /* ----------------------------------------------- one FAQ open at a time */
  var answers = document.querySelectorAll(".qa");
  answers.forEach(function (qa) {
    qa.addEventListener("toggle", function () {
      if (!qa.open) return;
      answers.forEach(function (other) {
        if (other !== qa) other.open = false;
      });
    });
  });
})();
