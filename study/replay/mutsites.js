// mutsites — the operator sites in TypeScript files that run: binary comparisons, equality,
// && and ||, + and -, a boolean literal outside a type, a prefix !.
//   node mutsites.js <path to the typescript package> <root> <file …>   → JSON [[file, start, old, new], …]
const ts = require(process.argv[2]);
const fs = require("fs");
const path = require("path");
const [root, ...files] = process.argv.slice(3);
const K = ts.SyntaxKind;
const SWAP = {
  [K.LessThanToken]: "<=", [K.LessThanEqualsToken]: "<", [K.GreaterThanToken]: ">=", [K.GreaterThanEqualsToken]: ">",
  [K.EqualsEqualsEqualsToken]: "!==", [K.ExclamationEqualsEqualsToken]: "===",
  [K.AmpersandAmpersandToken]: "||", [K.BarBarToken]: "&&", [K.PlusToken]: "-", [K.MinusToken]: "+",
};
const out = [];
for (const file of files) {
  const text = fs.readFileSync(path.join(root, file), "utf8");
  const src = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
  const visit = (node) => {
    if (ts.isTypeNode(node) && !ts.isExpressionWithTypeArguments(node)) return;   // types do not run
    if (ts.isBinaryExpression(node) && SWAP[node.operatorToken.kind] !== undefined) {
      const op = node.operatorToken;
      out.push([file, op.getStart(src), op.getText(src), SWAP[op.kind]]);
    } else if (node.kind === K.TrueKeyword || node.kind === K.FalseKeyword) {
      out.push([file, node.getStart(src), node.getText(src), node.kind === K.TrueKeyword ? "false" : "true"]);
    } else if (ts.isPrefixUnaryExpression(node) && node.operator === K.ExclamationToken) {
      out.push([file, node.getStart(src), "!", ""]);
    }
    ts.forEachChild(node, visit);
  };
  visit(src);
}
process.stdout.write(JSON.stringify(out));
