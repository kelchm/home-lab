export const isFixturePath = (input) => /(?:^|[\\/])fixtures(?:[\\/]|$)/.test(input);
// Grafana's built-in $__ macros are supported; standalone dashboard variables and their braced forms are not.
export const hasDashboardVariable = (expr) => /\$(?:\{(?!__)[a-zA-Z_]|(?!__)[a-zA-Z_])/.test(expr);
