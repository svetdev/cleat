// Conformance fixture: known escapes and one function of known complexity.
package sample

import "testing"

//nolint:errcheck
func read() {}

func TestSkipped(t *testing.T) { t.Skip("later") }

func branchy(n int) int {
	if n == 1 {
		return 1
	}
	if n == 2 {
		return 2
	}
	if n == 3 {
		return 3
	}
	if n == 4 {
		return 4
	}
	return 0
}
