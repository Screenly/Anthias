// Home-page screenshot slider. Authored in TypeScript; Hugo's js.Build
// pipeline transpiles + minifies via esbuild before serving. Loaded
// with `defer` from baseof.html so it never blocks the LCP image.
//
// Design constraints (all PageSpeed-sensitive):
//   - No layout reads/writes on scroll (IntersectionObserver only,
//     so the browser doesn't pay style/layout cost per frame as the
//     user swipes).
//   - Autoplay pauses when the slider is off-screen — we don't burn
//     CPU advancing a slider the user can't see.
//   - Autoplay is skipped entirely under prefers-reduced-motion;
//     the active pill simply stays filled and slides only advance
//     on user input.
//   - No DOM queries on every tick; everything resolves at init.
//
// Markup contract (see layouts/index.html):
//   [data-screenshot-slider]   — root element
//     [data-screenshot-track]  — horizontal scroll container
//       [data-screenshot-slide][data-slide-index][data-slide-url]
//     [data-screenshot-prev]   — previous button
//     [data-screenshot-next]   — next button
//     [data-screenshot-pill][data-slide-index] — bottom pills
//     [data-screenshot-url]    — URL pill text inside the chrome
//     [data-screenshot-counter] — "01" counter in the chrome

// Default autoplay cadence in ms — only used if the slider element
// hasn't set the `--autoplay-ms` custom property (which is the
// authoritative source: see .screenshot-slider in main.css). Reading
// the CSS variable at init keeps the JS timeout and the progress-bar
// animation duration in lock-step, so changing one in CSS doesn't
// leave the bar and the slide-advance silently disagreeing.
const AUTOPLAY_MS_FALLBACK = 6000
const URL_FADE_MS = 250

function readAutoplayMs(root: HTMLElement): number {
  const raw = getComputedStyle(root).getPropertyValue('--autoplay-ms').trim()
  if (!raw) return AUTOPLAY_MS_FALLBACK
  // CSS time values can be `<number>ms` or `<number>s`. Strip the unit
  // and convert. parseFloat is fine here — it stops at the first
  // non-numeric char and returns NaN for empty/bad input, which we
  // catch with the isFinite check below.
  const value = Number.parseFloat(raw)
  if (!Number.isFinite(value) || value <= 0) return AUTOPLAY_MS_FALLBACK
  return raw.endsWith('s') && !raw.endsWith('ms') ? value * 1000 : value
}

interface SliderRefs {
  root: HTMLElement
  track: HTMLElement
  slides: HTMLElement[]
  pills: HTMLButtonElement[]
  prev: HTMLButtonElement | null
  next: HTMLButtonElement | null
  url: HTMLElement | null
  counter: HTMLElement | null
}

function collect(root: HTMLElement): SliderRefs | null {
  const track = root.querySelector<HTMLElement>('[data-screenshot-track]')
  if (!track) return null
  const slides = Array.from(
    track.querySelectorAll<HTMLElement>('[data-screenshot-slide]'),
  )
  if (slides.length === 0) return null
  return {
    root,
    track,
    slides,
    pills: Array.from(
      root.querySelectorAll<HTMLButtonElement>('[data-screenshot-pill]'),
    ),
    prev: root.querySelector<HTMLButtonElement>('[data-screenshot-prev]'),
    next: root.querySelector<HTMLButtonElement>('[data-screenshot-next]'),
    url: root.querySelector<HTMLElement>('[data-screenshot-url]'),
    counter: root.querySelector<HTMLElement>('[data-screenshot-counter]'),
  }
}

function init(root: HTMLElement): void {
  const refs = collect(root)
  if (!refs) return

  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches
  const autoplayMs = readAutoplayMs(root)
  // -1 (not 0) so the initial setActive(0) call applies state to the
  // first pill — otherwise the early-return on equal-index swallows
  // the very first state assignment and the autoplay bar never starts.
  let activeIndex = -1
  let autoplayTimer: number | undefined
  // Tracks the pending URL-fade text swap so a rapid second slide
  // change can cancel the prior pending swap — otherwise a stale
  // timeout could fire later and briefly clobber the URL pill with
  // the previous slide's value.
  let urlFadeTimer: number | undefined
  // Tracks whether the user has *interacted* with the slider. After
  // they click prev/next or a pill we stop autoplay entirely — the
  // assumption is they want to inspect the slide, not be swept along.
  let userInteracted = false
  let isVisible = true

  const setActive = (index: number): void => {
    if (index === activeIndex) return
    activeIndex = index

    refs.pills.forEach((pill, i) => {
      const selected = i === index
      // aria-current marks the active pill; presence vs absence is
      // what assistive tech tests for, so remove on the inactive
      // pills rather than setting an explicit "false".
      if (selected) {
        pill.setAttribute('aria-current', 'true')
      } else {
        pill.removeAttribute('aria-current')
      }
      // Drive the progress-bar animation purely through CSS selector
      // state: the @keyframes only attaches to .screenshot-pill with
      // `aria-current="true"` and `data-state="playing"`, so removing
      // `data-state` from the previous pill (the else branch) is what
      // restarts the animation when the same pill goes active again —
      // no manual DOM detach/reattach or animation-name juggling.
      if (selected) {
        if (userInteracted || reducedMotion) {
          pill.dataset.state = reducedMotion ? 'static' : 'paused'
          pill.style.setProperty('--paused-progress', '100%')
        } else {
          pill.dataset.state = 'playing'
        }
      } else {
        delete pill.dataset.state
        pill.style.removeProperty('--paused-progress')
      }
    })

    // Chrome URL pill. Brief fade so the text swap doesn't read as a
    // glitch. Don't animate when reduced-motion is on. Cancel any
    // still-pending swap before scheduling a new one so rapid slide
    // changes (button mashing, swipe + observer) can't leave a stale
    // older timeout to fire and overwrite the URL with a past value.
    if (refs.url) {
      const slug =
        refs.slides[index]?.dataset.slideUrl?.toLowerCase() ?? 'anthias.local'
      const nextUrl =
        slug === 'home' || !slug ? 'anthias.local' : `anthias.local/${slug}`
      if (urlFadeTimer !== undefined) {
        window.clearTimeout(urlFadeTimer)
        urlFadeTimer = undefined
      }
      if (reducedMotion) {
        refs.url.textContent = nextUrl
      } else {
        refs.url.classList.add('screenshot-url-fade')
        urlFadeTimer = window.setTimeout(() => {
          urlFadeTimer = undefined
          if (refs.url) {
            refs.url.textContent = nextUrl
            refs.url.classList.remove('screenshot-url-fade')
          }
        }, URL_FADE_MS)
      }
    }
    if (refs.counter) {
      refs.counter.textContent = String(index + 1).padStart(2, '0')
    }

    refs.prev?.toggleAttribute('disabled', false)
    refs.next?.toggleAttribute('disabled', false)
  }

  const scrollTo = (index: number, behavior: ScrollBehavior): void => {
    const target = refs.slides[index]
    if (!target) return
    // scrollIntoView gives us snapping for free on all evergreen
    // browsers; scroll-snap aligns the slide to start as configured
    // in CSS. Using { inline: 'start' } avoids a vertical-axis quirk
    // where 'nearest' picks a non-snapping resting position.
    target.scrollIntoView({
      behavior: reducedMotion ? 'auto' : behavior,
      inline: 'start',
      block: 'nearest',
    })
  }

  const goTo = (index: number, fromUser: boolean): void => {
    const wrapped = ((index % refs.slides.length) + refs.slides.length) %
      refs.slides.length
    if (fromUser) {
      userInteracted = true
      stopAutoplay()
    }
    scrollTo(wrapped, 'smooth')
    setActive(wrapped)
  }

  const stopAutoplay = (): void => {
    if (autoplayTimer !== undefined) {
      window.clearTimeout(autoplayTimer)
      autoplayTimer = undefined
    }
  }

  const scheduleAutoplay = (): void => {
    if (reducedMotion || userInteracted) return
    if (refs.slides.length <= 1) return
    if (!isVisible) return
    stopAutoplay()
    autoplayTimer = window.setTimeout(() => {
      goTo(activeIndex + 1, false)
      scheduleAutoplay()
    }, autoplayMs)
  }

  // IntersectionObserver tracks which slide is centered so dot state
  // stays in sync with manual horizontal scrolling / swipe gestures.
  // 0.55 threshold == "more than half of this slide is in the
  // viewport"; cheaper and more stable than scroll-event polling.
  const slideObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && entry.intersectionRatio > 0.55) {
          const idx = Number(
            (entry.target as HTMLElement).dataset.slideIndex ?? '0',
          )
          setActive(idx)
        }
      }
    },
    { root: refs.track, threshold: [0.55] },
  )
  refs.slides.forEach((slide) => slideObserver.observe(slide))

  // Pause autoplay when the slider is off-screen — saves CPU below
  // the fold and avoids a "race" where the page loads, the slider
  // ticks twice before the user scrolls down, and they land on
  // slide 3 instead of 1. Delegating to hoverPause/hoverResume keeps
  // the CSS progress bar (data-state) and the JS timer in lock-step;
  // stopping the timer alone would let the bar keep advancing while
  // off-screen and desync from the fresh timer on resume.
  const visibilityObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        isVisible = entry.isIntersecting
        if (isVisible) hoverResume()
        else hoverPause()
      }
    },
    { threshold: 0.2 },
  )

  refs.prev?.addEventListener('click', () => goTo(activeIndex - 1, true))
  refs.next?.addEventListener('click', () => goTo(activeIndex + 1, true))
  refs.pills.forEach((pill) => {
    pill.addEventListener('click', () => {
      const idx = Number(pill.dataset.slideIndex ?? '0')
      goTo(idx, true)
    })
  })

  // Keyboard nav when the track itself has focus. Arrow keys move
  // one slide at a time; Home/End jump to the ends. Stops the
  // browser from also scrolling the page.
  refs.track.addEventListener('keydown', (event) => {
    switch (event.key) {
      case 'ArrowLeft':
        event.preventDefault()
        goTo(activeIndex - 1, true)
        break
      case 'ArrowRight':
        event.preventDefault()
        goTo(activeIndex + 1, true)
        break
      case 'Home':
        event.preventDefault()
        goTo(0, true)
        break
      case 'End':
        event.preventDefault()
        goTo(refs.slides.length - 1, true)
        break
    }
  })

  // Hover/focus pause feels much better than blind autoplay — gives
  // the user a chance to actually read a caption.
  const hoverPause = (): void => {
    if (userInteracted) return
    stopAutoplay()
    const pill = refs.pills[activeIndex]
    if (pill && !reducedMotion) {
      // Snapshot the current animated width before swapping into
      // paused mode so the bar holds where it is instead of jumping
      // to 0% or 100%.
      const fill = pill.querySelector<HTMLElement>('.screenshot-pill-fill')
      if (fill) {
        const computed = getComputedStyle(fill).width
        const containerWidth = pill.clientWidth
        if (containerWidth > 0 && computed.endsWith('px')) {
          const pct = (parseFloat(computed) / containerWidth) * 100
          pill.style.setProperty('--paused-progress', `${pct.toFixed(1)}%`)
        }
      }
      pill.dataset.state = 'paused'
    }
  }
  const hoverResume = (): void => {
    if (userInteracted || reducedMotion) return
    const pill = refs.pills[activeIndex]
    if (pill) {
      pill.dataset.state = 'playing'
      pill.style.removeProperty('--paused-progress')
    }
    scheduleAutoplay()
  }
  refs.root.addEventListener('mouseenter', hoverPause)
  refs.root.addEventListener('mouseleave', hoverResume)
  refs.root.addEventListener('focusin', hoverPause)
  // focusout bubbles, so a child-to-child focus shift inside the
  // slider (e.g. track → next button) would otherwise re-trigger
  // resume mid-keyboard-nav. Skip when focus is still inside the
  // slider; only treat focus actually leaving the root as "resume".
  refs.root.addEventListener('focusout', (event) => {
    const next = (event as FocusEvent).relatedTarget as Node | null
    if (next && refs.root.contains(next)) return
    hoverResume()
  })

  // Attach the visibility observer last — its callback dispatches to
  // hoverPause/hoverResume, which must be defined first.
  visibilityObserver.observe(refs.root)

  // Initial state: pill 0 active, autoplay armed.
  setActive(0)
  scheduleAutoplay()
}

function boot(): void {
  document
    .querySelectorAll<HTMLElement>('[data-screenshot-slider]')
    .forEach(init)
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true })
} else {
  boot()
}
