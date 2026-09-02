# Conformance fixture: known escapes and one function of known complexity.
# rubocop:disable Style/Foo
def read
  skip "later"
end

def branchy(n)
  return 1 if n == 1
  return 2 if n == 2
  return 3 if n == 3
  return 4 if n == 4
  0
end
