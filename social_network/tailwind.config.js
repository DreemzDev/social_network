/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./templates/**/*.html',
  './node_modules/flowbite/**/*.js',
  './node_modules/emojioneArea/**/*/.js',

],
  theme: {
    extend: {},
  },
  plugins: [require('flowbite/plugin')],
  
}

