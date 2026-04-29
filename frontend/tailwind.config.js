window.tailwind = window.tailwind || {};
window.tailwind.config = {
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "surface-dim": "#d9dadb", "surface": "#f8f9fa", "on-background": "#191c1d", "inverse-on-surface": "#f0f1f2", "tertiary": "#001902", "surface-container-lowest": "#ffffff", "surface-container": "#edeeef", "primary-container": "#002366", "secondary": "#506600", "on-primary-fixed-variant": "#2a4386", "secondary-fixed": "#c3f400", "tertiary-fixed": "#a3f69c", "tertiary-container": "#003007", "background": "#f8f9fa", "on-error-container": "#93000a", "primary-fixed": "#dbe1ff", "inverse-primary": "#b3c5ff", "outline-variant": "#c5c6d2", "primary": "#00113a", "on-tertiary-container": "#51a050", "secondary-container": "#c1f100", "surface-container-high": "#e7e8e9", "on-error": "#ffffff", "on-primary-container": "#758dd5", "on-secondary-container": "#546b00", "surface-bright": "#f8f9fa", "error-container": "#ffdad6", "on-tertiary-fixed-variant": "#005312", "error": "#ba1a1a", "on-surface": "#191c1d", "primary-fixed-dim": "#b3c5ff", "on-primary": "#ffffff", "outline": "#757682", "surface-container-highest": "#e1e3e4", "tertiary-fixed-dim": "#88d982", "surface-container-low": "#f3f4f5", "on-secondary-fixed": "#161e00", "on-tertiary": "#ffffff", "on-tertiary-fixed": "#002204", "on-surface-variant": "#444650", "on-secondary-fixed-variant": "#3c4d00", "on-secondary": "#ffffff", "inverse-surface": "#2e3132", "surface-variant": "#e1e3e4", "surface-tint": "#435b9f", "secondary-fixed-dim": "#abd600", "on-primary-fixed": "#00174a"
      },
      borderRadius: { "DEFAULT": "0.125rem", "lg": "0.25rem", "xl": "0.5rem", "full": "0.75rem" },
      spacing: { "xs": "4px", "xxl": "80px", "md": "16px", "lg": "24px", "sm": "8px", "unit": "4px", "xl": "48px", "container-max": "1280px", "gutter": "24px" },
      fontFamily: { "stats-num": ["Lexend"], "body-md": ["Work Sans"], "headline-md": ["Lexend"], "display-xl": ["Lexend"], "headline-lg": ["Lexend"], "label-bold": ["Lexend"], "body-lg": ["Work Sans"], "label-sm": ["Work Sans"] },
      fontSize: {
        "stats-num": ["40px", {"lineHeight": "1", "letterSpacing": "-0.03em", "fontWeight": "800"}],
        "body-md": ["16px", {"lineHeight": "1.5", "fontWeight": "400"}],
        "headline-md": ["24px", {"lineHeight": "1.3", "fontWeight": "700"}],
        "display-xl": ["48px", {"lineHeight": "1.1", "letterSpacing": "-0.02em", "fontWeight": "800"}],
        "headline-lg": ["32px", {"lineHeight": "1.2", "letterSpacing": "-0.01em", "fontWeight": "700"}],
        "label-bold": ["14px", {"lineHeight": "1.2", "letterSpacing": "0.05em", "fontWeight": "600"}],
        "body-lg": ["18px", {"lineHeight": "1.6", "fontWeight": "400"}],
        "label-sm": ["12px", {"lineHeight": "1.2", "fontWeight": "500"}]
      }
    }
  }
};
