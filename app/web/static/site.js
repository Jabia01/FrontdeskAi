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
    var conversations = [];
    try {
      conversations = JSON.parse(
        document.getElementById("demo-conversations").textContent
      );
    } catch (e) {
      conversations = [];
    }

    var phone = document.querySelector("[data-phone]");
    var langLabel = document.querySelector("[data-current-language]");
    var chips = Array.prototype.slice.call(
      document.querySelectorAll("[data-phone-langs] .lang-chip")
    );
    var ownerWindow = document.querySelector("[data-owner-window]");
    var current = 0;
    var run = 0; /* bumped on every switch so an abandoned replay stops itself */

    var bubble = function (turn, rtl) {
      var el = document.createElement("div");
      el.className =
        "bubble " + (turn.from === "customer" ? "customer" : "chichi") +
        (turn.booked ? " booked" : "");
      if (rtl) el.setAttribute("dir", "rtl");
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

    /* Keep the thread inside the frame: drop the oldest as each new one lands. */
    var trim = function () {
      while (thread.scrollHeight > thread.clientHeight && thread.children.length > 1) {
        thread.removeChild(thread.firstElementChild);
      }
    };

    var wait = function (ms) {
      return new Promise(function (resolve) { setTimeout(resolve, ms); });
    };

    var dress = function (conversation, index) {
      if (phone) phone.setAttribute("data-accent", conversation.accent);
      if (langLabel) langLabel.textContent = conversation.label;
      chips.forEach(function (chip, i) {
        chip.classList.toggle("on", i === index);
      });
      if (ownerWindow) ownerWindow.classList.remove("in");
    };

    var play = function (index) {
      current = index;
      run += 1;
      var mine = run;
      var conversation = conversations[index];
      thread.innerHTML = "";
      dress(conversation, index);

      if (still) {
        conversation.turns.forEach(function (turn) {
          var el = bubble(turn, conversation.rtl);
          el.style.animation = "none";
          el.style.opacity = 1;
          el.style.transform = "none";
          thread.appendChild(el);
        });
        trim();
        if (ownerWindow) ownerWindow.classList.add("in");
        return;
      }

      var step = function (at) {
        if (mine !== run) return null; /* another conversation took over */
        if (at >= conversation.turns.length) {
          return wait(4200).then(function () {
            if (mine !== run) return null;
            return play((index + 1) % conversations.length);
          });
        }
        var turn = conversation.turns[at];
        return wait(turn.from === "customer" ? 850 : 300)
          .then(function () {
            if (mine !== run || turn.from === "customer") return null;
            var dots = typing();
            thread.appendChild(dots);
            trim();
            return wait(1250).then(function () {
              if (dots.parentNode) dots.parentNode.removeChild(dots);
            });
          })
          .then(function () {
            if (mine !== run) return null;
            thread.appendChild(bubble(turn, conversation.rtl));
            trim();
            /* the owner's phone buzzes on the booking beat */
            if (turn.booked && ownerWindow) {
              setTimeout(function () { ownerWindow.classList.add("in"); }, 500);
            }
            return step(at + 1);
          });
      };

      return step(0);
    };

    chips.forEach(function (chip, i) {
      chip.addEventListener("click", function () { play(i); });
    });

    /* Nobody should pay battery to animate a phone they cannot see. */
    if (conversations.length) {
      if ("IntersectionObserver" in window) {
        var watcher = new IntersectionObserver(function (entries) {
          if (entries[0].isIntersecting) {
            watcher.disconnect();
            play(0);
          }
        }, { threshold: 0.3 });
        watcher.observe(thread);
      } else {
        play(0);
      }
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
