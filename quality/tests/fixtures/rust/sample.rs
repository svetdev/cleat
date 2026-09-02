// Conformance fixture: known escapes and one function of known complexity.
#![allow(dead_code)]

pub fn read() -> i32 {
    let v: Result<i32, ()> = Ok(1);
    let a = v.unwrap();
    let b = v.expect("never");
    unsafe { a + b }
}

pub fn later() { todo!() }

#[ignore]
fn skipped() {}

pub fn branchy(n: i32) -> i32 {
    if n == 1 { return 1; }
    if n == 2 { return 2; }
    if n == 3 { return 3; }
    if n == 4 { return 4; }
    0
}
