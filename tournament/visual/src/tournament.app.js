/* Tournament shell — World-Cup bracket + leaderboard over the 3D arena.
 * Reads window.__TOURNAMENT__ (bracket topology + every match's frames), drives
 * the arena (window.RW) one match at a time, and reveals winners + the next
 * round as the operator clicks through. Pure DOM; no build step of its own. */
(function () {
  "use strict";
  const T = window.__TOURNAMENT__;
  if (!T) { console.error("no __TOURNAMENT__ payload"); return; }

  const $ = (id) => document.getElementById(id);
  const colorOf = {};
  T.teams.forEach((t) => { colorOf[t.name] = t.color; });

  const played = new Set();            // labels of matches whose replay has finished
  const rounds = T.rounds;             // [{round, name, games:[...]}]
  const totalRounds = rounds.length;

  const nonByeGames = (r) => r.games.filter((g) => !g.bye);
  const roundComplete = (r) => nonByeGames(r).every((g) => played.has(g.label));
  // First round still holding an unplayed real match; totalRounds+1 once done.
  function currentRound() {
    for (const r of rounds) if (!roundComplete(r)) return r.round;
    return totalRounds + 1;
  }
  // Next match to play, scanning in bracket order (skips byes + played).
  function nextGame() {
    for (const r of rounds)
      for (const g of r.games)
        if (!g.bye && !played.has(g.label)) return { round: r, game: g };
    return null;
  }

  // ---- render bracket tree ----------------------------------------------
  function slotEl(name, cls) {
    const el = document.createElement("div");
    el.className = "slot " + cls;
    const c = colorOf[name];
    const crest = name
      ? `<span class="crest" style="background:${c};color:#06101d;box-shadow:0 0 11px ${c}aa">${name[0].toUpperCase()}</span>`
      : `<span class="crest">?</span>`;
    el.innerHTML = crest + `<span class="nm">${name || "—"}</span><span class="sc"></span>`;
    return el;
  }

  // SVG connector layer between rounds (drawn after the DOM lays out)
  const tieEls = [];              // tieEls[roundIdx][gameIdx] = tie element (or champ box)
  function drawConnectors() {
    const host = $("bracket");
    let svg = document.getElementById("bracket-svg");
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.id = "bracket-svg";
      host.insertBefore(svg, host.firstChild);
    }
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const hb = host.getBoundingClientRect();
    const ox = -hb.left + host.scrollLeft, oy = -hb.top + host.scrollTop;
    svg.setAttribute("width", host.scrollWidth);
    svg.setAttribute("height", host.scrollHeight);
    const line = (sx, sy, tx, ty, on) => {
      const midX = (sx + tx) / 2;
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", `M ${sx} ${sy} H ${midX} V ${ty} H ${tx}`);
      p.setAttribute("fill", "none");
      p.setAttribute("stroke", on ? "#2ec4b6" : "#26334f");
      p.setAttribute("stroke-width", on ? "2.5" : "1.5");
      svg.appendChild(p);
    };
    for (let rr = 0; rr < tieEls.length - 1; rr++) {
      (tieEls[rr] || []).forEach((src, k) => {
        const dst = (tieEls[rr + 1] || [])[Math.floor(k / 2)];
        if (!src || !dst) return;
        const s = src.getBoundingClientRect(), d = dst.getBoundingClientRect();
        const lit = src.classList.contains("won") && dst.dataset.filled === "1";
        line(s.right + ox, s.top + oy + s.height / 2, d.left + ox, d.top + oy + d.height / 2, lit);
      });
    }
  }

  function renderBracket() {
    const cur = currentRound();
    const wrap = $("bracket");
    wrap.innerHTML = "";
    tieEls.length = 0;
    const nxt = nextGame();

    rounds.forEach((r, rr) => {
      tieEls[rr] = [];
      const col = document.createElement("div");
      col.className = "round" + (r.round === cur ? " current" : "");
      const title = document.createElement("div");
      title.className = "round-title";
      title.textContent = r.name;
      col.appendChild(title);
      const ties = document.createElement("div");
      ties.className = "round-ties";
      col.appendChild(ties);

      const revealed = r.round <= cur;                 // participants known?
      r.games.forEach((g, gi) => {
        const tie = document.createElement("div");
        tie.className = "tie";
        tieEls[rr][gi] = tie;
        const isPlayed = played.has(g.label);
        const isNext = nxt && nxt.game.label === g.label;

        if (g.bye) {
          tie.classList.add("won");
          if (revealed) tie.dataset.filled = "1";
          tie.appendChild(slotEl(revealed ? g.team_a : null, revealed ? "win" : "tbd"));
          const m = document.createElement("div"); m.className = "meta";
          m.innerHTML = revealed ? `<span class="bye-tag">BYE · ADVANCES</span>` : "TBD";
          tie.appendChild(m);
          ties.appendChild(tie);
          return;
        }

        if (!revealed) {                                // future round: hide names
          tie.classList.add("pending");
          tie.appendChild(slotEl(null, "tbd")); tie.appendChild(slotEl(null, "tbd"));
          const m = document.createElement("div"); m.className = "meta"; m.textContent = "TBD";
          tie.appendChild(m);
          ties.appendChild(tie);
          return;
        }

        tie.dataset.filled = "1";
        const aWon = isPlayed && g.winner === g.team_a;
        const bWon = isPlayed && g.winner === g.team_b;
        if (isPlayed) tie.classList.add("won");
        tie.appendChild(slotEl(g.team_a, isPlayed ? (aWon ? "win" : "lose") : ""));
        tie.appendChild(slotEl(g.team_b, isPlayed ? (bWon ? "win" : "lose") : ""));
        const m = document.createElement("div"); m.className = "meta";
        if (isPlayed) {
          m.textContent = `${g.reason} · ${g.ticks} ticks`;
        } else if (isNext) {
          tie.classList.add("playable");
          tie.onclick = () => playGame(r, g);
          m.textContent = "▶ CLICK TO PLAY";
        } else {
          m.textContent = "UPCOMING";
        }
        ties.appendChild(tie);
      });
      wrap.appendChild(col);
    });

    // champion column (its own tie row so a connector can reach it)
    const done = cur > totalRounds;
    const cc = document.createElement("div");
    cc.className = "round champ-col";
    cc.innerHTML = `<div class="round-title">Champion</div>`;
    const cties = document.createElement("div"); cties.className = "round-ties";
    const box = document.createElement("div");
    box.className = "champ-box tie" + (done ? "" : " tbd");
    if (done) box.dataset.filled = "1";
    box.innerHTML = `<div class="lbl">Champion</div><div class="cup">🏆</div>` +
      `<div class="nm">${done ? T.champion : "TBD"}</div>`;
    cties.appendChild(box); cc.appendChild(cties); wrap.appendChild(cc);
    tieEls[rounds.length] = [box];

    renderLeaderboard();
    $("awardsbtn").hidden = !done;         // awards unlock once the champion is known
    const ng = nextGame();
    const btn = $("playnext");
    btn.disabled = !ng;
    btn.textContent = ng ? `▶ Play next: ${ng.game.team_a} vs ${ng.game.team_b}` : "🏆 Tournament complete";
    $("subtag").textContent = `Harris All Hands · ${T.teams.length} teams · ${T.map} · seed ${T.seed}`;
    requestAnimationFrame(drawConnectors);
  }

  // ---- leaderboard -------------------------------------------------------
  function renderLeaderboard() {
    const rec = {};
    T.teams.forEach((t) => { rec[t.name] = { name: t.name, w: 0, l: 0, out: false }; });
    rounds.forEach((r) => r.games.forEach((g) => {
      if (g.bye || !played.has(g.label)) return;
      rec[g.winner] && rec[g.winner].w++;
      const loser = g.winner === g.team_a ? g.team_b : g.team_a;
      if (rec[loser]) { rec[loser].l++; rec[loser].out = true; }
    }));
    const done = currentRound() > totalRounds;
    const rows = Object.values(rec).sort((a, b) =>
      (a.out - b.out) || (b.w - a.w) || a.name.localeCompare(b.name));

    const box = $("lb-rows");
    box.innerHTML = "";
    rows.forEach((t, i) => {
      const isChamp = done && t.name === T.champion;
      const el = document.createElement("div");
      el.className = "lb-row" + (t.out ? " out" : "") + (isChamp ? " champ" : "");
      el.innerHTML =
        `<span class="rank">${isChamp ? "🏆" : i + 1}</span>` +
        `<span class="dot" style="background:${colorOf[t.name]};color:${colorOf[t.name]}"></span>` +
        `<span class="nm">${t.name}</span>` +
        `<span class="rec">${t.w}–${t.l}${t.out ? "" : " · in"}</span>`;
      box.appendChild(el);
    });

    // the always-on strip pinned above both views mirrors the same standings
    const strip = $("lb-strip");
    strip.innerHTML = `<span class="lbs-label">🏆 Leaderboard</span>`;
    rows.forEach((t, i) => {
      const isChamp = done && t.name === T.champion;
      const el = document.createElement("span");
      el.className = "lbs-pill" + (t.out ? " out" : "") + (isChamp ? " champ" : "");
      el.innerHTML =
        `<span class="dot" style="background:${colorOf[t.name]};color:${colorOf[t.name]}"></span>` +
        `<span>${isChamp ? "🏆 " : (i + 1) + ". "}${t.name}</span>` +
        `<span class="rec">${t.w}–${t.l}</span>`;
      strip.appendChild(el);
    });
  }

  // ---- play a match in the arena ----------------------------------------
  function playGame(r, g) {
    const match = T.matches[g.label];
    if (!match) { console.error("no frames for", g.label); return; }
    $("mtitle").textContent = `${r.name} · ${g.team_a} vs ${g.team_b}`;
    document.body.classList.add("playing");
    // arena.app.js sized its canvas while hidden (0×0); nudge it to re-measure.
    window.dispatchEvent(new Event("resize"));
    const jsonl = match.frames.map((f) => JSON.stringify(f)).join("\n");
    window.RW.onMatchEnd = () => {
      played.add(g.label);                 // reveal winner + unlock next round
      renderBracket();
    };
    window.RW.loadText(jsonl);             // loads + auto-plays
  }

  function backToBracket() {
    if (window.RW) { window.RW.onMatchEnd = null; window.RW.play(false); }  // drop stale handler
    document.body.classList.remove("playing");
    renderBracket();
  }

  $("playnext").onclick = () => { const ng = nextGame(); if (ng) playGame(ng.round, ng.game); };
  $("back").onclick = backToBracket;

  // ---- end-of-day awards: champion + honorable mentions typed live --------
  $("awardsbtn").onclick = () => {
    $("aw-champ").textContent = T.champion;
    $("awards").classList.add("show");
  };
  $("aw-close").onclick = () => $("awards").classList.remove("show");
  $("aw-add").onclick = () => {
    const d = document.createElement("div");
    d.className = "aw-mention";
    d.contentEditable = "true";
    $("aw-hm").appendChild(d);
    d.focus();
  };
  window.addEventListener("resize", () => { if (!document.body.classList.contains("playing")) drawConnectors(); });

  renderBracket();
})();
