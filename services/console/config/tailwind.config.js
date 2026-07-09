module.exports = {
  content: [
    './public/*.html',
    './app/helpers/**/*.rb',
    './app/javascript/**/*.js',
    './app/views/**/*.{erb,haml,html,slim}'
  ],
  theme: {
    extend: {
      colors: {
        // Light-console semantics: existing templates were written for a dark
        // theme, so the numeric ramps below preserve class intent rather than
        // Tailwind's default light-to-dark meaning.
        centaur: {
          50: '#e8faf0', 100: '#155330', 200: '#18653a',
          300: '#1a8147', 400: '#1ea358', 500: '#28c26a',
          600: '#1ea358', 700: '#1a8147', 800: '#18653a', 900: '#155330'
        },
        zinc: {
          50: '#111827', 100: '#18181b', 200: '#27272a',
          300: '#3f3f46', 400: '#52525b', 500: '#71717a',
          600: '#a1a1aa', 700: '#d4d4d8', 800: '#e4e4e7',
          900: '#f4f4f5', 950: '#fafafa'
        },
        // Light neutral surfaces mapped onto the app's existing ink classes.
        ink: {
          950: '#f8fafc', 900: '#f6f7f9', 850: '#ffffff', 800: '#f9fafb',
          700: '#ffffff', 600: '#d9dee7', 500: '#b6c0cc'
        }
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace']
      }
    },
    // Very small radii everywhere for the sharp, terminal-ish look.
    borderRadius: {
      none: '0px', sm: '1px', DEFAULT: '2px', md: '2px',
      lg: '2px', xl: '2px', '2xl': '3px', '3xl': '3px', full: '2px'
    }
  },
  plugins: []
}
