module.exports = {
  content: ['index.html', 'src/app.js'],
  theme: {
    extend: {
      colors: {
        accent: {
          DEFAULT: '#7c3aed',
          500: '#7c3aed',
          600: '#6d28d9',
          400: '#8b5cf6',
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Inter', 'SF Pro Text', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      maxWidth: {
        'col': '48rem',
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
};
