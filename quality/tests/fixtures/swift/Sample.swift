// Conformance fixture: known escapes and one function of known complexity.
import Foundation

let data = try! JSONSerialization.data(withJSONObject: [1])
let text = "x" as! String
let count = Optional(1)!.description
// swiftlint:disable force_cast
final class Box: @unchecked Sendable {}
let notAnEscape = "not!"  // a bang inside a string is just text

func branchy(_ n: Int) -> Int {
    if n == 1 { return 1 }
    if n == 2 { return 2 }
    if n == 3 { return 3 }
    if n == 4 { return 4 }
    return 0
}
