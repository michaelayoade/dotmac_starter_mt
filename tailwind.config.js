/** @type {import('tailwindcss').Config} */

/*
 * Safelist convention (ported from SUB:tailwind.config.js).
 *
 * Tailwind's content scanner only picks up class names that appear as
 * LITERAL text somewhere under `content` below. Any template that builds a
 * class name by interpolating a variable — e.g. `bg-{{ color }}-500` — is
 * invisible to the scanner and gets purged from the production build even
 * though it renders fine in dev. `templates/components/table_macros.html`'s
 * status_badge() macro currently avoids this (its color_map values are
 * written out as full literal strings), but as more admin screens land
 * (roles, audit, custom fields) some will likely want a `color` parameter
 * threaded into a macro the way ST's `templates/components/macros.html` UI
 * kit does. When that happens, extend MACRO_COLORS/the safelist patterns
 * below instead of re-deriving the convention — don't hardcode a new class
 * list per screen.
 */
const MACRO_COLORS = ["primary", "accent", "slate", "green", "red", "yellow", "blue"];

module.exports = {
  content: ["./templates/**/*.html", "./static/js/**/*.js"],
  safelist: MACRO_COLORS.flatMap((c) => [
    `bg-${c}-50`,
    `bg-${c}-100`,
    `bg-${c}-500`,
    `bg-${c}-600`,
    `text-${c}-600`,
    `text-${c}-700`,
    `border-${c}-200`,
    `border-${c}-300`,
    `from-${c}-500`,
    `to-${c}-500`,
    `dark:bg-${c}-900/20`,
    `dark:bg-${c}-900/30`,
    `dark:text-${c}-300`,
    `dark:text-${c}-400`,
  ]),
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Plus Jakarta Sans", "system-ui", "sans-serif"],
        display: ["Outfit", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
