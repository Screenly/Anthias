/**
 * CSS tagged template literal helper function
 * @param {TemplateStringsArray} strings - The template literal strings
 * @param {...any} values - The template literal values
 * @returns {string} The processed CSS string
 */
export const css = (strings, ...values) => {
  return strings.reduce((result, string, i) => {
    return result + string + (values[i] || '')
  }, '')
}
