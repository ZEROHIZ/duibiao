# Design System

This document specifies the visual theme, color tokens, typography, and component specs for the **Blogger Distiller Web Dashboard**.

---

## 🎨 Color Strategy: Restrained Editorial

We use a **committed editorial color strategy** anchored on a warm paper neutral and a rich Terracotta/Crimson accent.

### Color Tokens (OKLCH)

```css
:root {
  /* Light Mode - Warm Paper & Ink */
  --bg-primary: oklch(98% 0.006 70);       /* Soft Warm Paper */
  --bg-secondary: oklch(95% 0.008 70);     /* Soft card background */
  --ink-primary: oklch(22% 0.012 70);      /* Charcoal Ink */
  --ink-secondary: oklch(45% 0.01 70);     /* Muted Charcoal */
  --ink-tertiary: oklch(65% 0.008 70);     /* Light Warm Gray */
  
  --accent-primary: oklch(45% 0.055 28);    /* Deep Terracotta Crimson */
  --accent-secondary: oklch(55% 0.045 35);  /* Muted Clay */
  --accent-light: oklch(92% 0.015 28);      /* Light terracotta wash */
  
  --border-primary: oklch(88% 0.008 70);    /* Thin divider gray */
  --border-focus: oklch(45% 0.055 28);
  
  /* Layout constraints */
  --max-content-width: 72ch;
  --max-page-width: 1400px;
}

[data-theme="dark"] {
  /* Dark Mode - Muted Charcoal Ink & Warm Gold Accent */
  --bg-primary: oklch(14% 0.008 70);       /* Warm Deep Charcoal */
  --bg-secondary: oklch(18% 0.01 70);      /* Card gray */
  --ink-primary: oklch(92% 0.006 70);      /* Warm Paper Text */
  --ink-secondary: oklch(75% 0.008 70);    /* Muted Text */
  --ink-tertiary: oklch(55% 0.008 70);     /* Darker Gray */
  
  --accent-primary: oklch(75% 0.04 70);     /* Warm Gold / Ochre */
  --accent-secondary: oklch(65% 0.03 70);   /* Muted Sand Gold */
  --accent-light: oklch(22% 0.015 70);
  
  --border-primary: oklch(26% 0.01 70);     /* Dark divider */
  --border-focus: oklch(75% 0.04 70);
}
```

---

## ✍️ Typography

We employ a contrast axis between a **Classic Serif** for headers and editorial quotes, and a **Neutral Sans-Serif** for structured tables, data values, and inputs.

- **Primary Heading (Serif)**: `Playfair Display`, `Georgia`, `serif`
- **Body & Data (Sans-Serif)**: `Inter`, `-apple-system`, `BlinkMacSystemFont`, `system-ui`, `sans-serif`

### Scale and Constraints
- **Heading 1**: `font-size: clamp(2rem, 4vw, 3.5rem); font-family: var(--font-serif); font-weight: 400;`
- **Heading 2**: `font-size: clamp(1.5rem, 2.5vw, 2.2rem); font-family: var(--font-serif);`
- **Body Text**: `font-size: 1rem; line-height: 1.6; max-width: var(--max-content-width);`
- **Eyebrow (Banned reflex check)**: Banned from using "uppercase widely tracked small headers" above every section. Use natural casing, variable weights, or elegant borders instead.

---

## 🏛️ Layout & Component Specs

### 1. Page Shell
- A left-aligned vertical layout or simple horizontal header navigation with asymmetric white space.
- Distinct tabs representing the Dashboard and the 4 Monitor Feeds.

### 2. Cards (The Anti-SaaS Card Rule)
- Avoid colored left borders (`border-left: 4px solid ...`) or solid border shadow-heavy boxes.
- Cards are represented as borderless blocks with a very thin bottom separator: `border-bottom: 1px solid var(--border-primary)`.
- Hover state: Accent color shifts on the text elements, rather than scale animations on card blocks.

### 3. Motion (GSAP Driven)
- **Entrance Animation**: Simple stagger fade-in:
  ```javascript
  gsap.from(".card-item", {
    opacity: 0,
    y: 20,
    duration: 0.8,
    stagger: 0.1,
    ease: "power2.out"
  });
  ```
- **Tab Transitions**: Smooth cross-fade.
- **Prefers-reduced-motion**: Disable GSAP transitions if system media query is active:
  ```css
  @media (prefers-reduced-motion: reduce) {
    * {
      transition: none !important;
      animation: none !important;
    }
  }
  ```
