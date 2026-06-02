/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0b0f14",
        panel: "#121822",
        edge: "#1f2a37",
      },
    },
  },
  plugins: [],
};
