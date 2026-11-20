#[cfg(unix)]
struct Unreadable(PathBuf);

#[cfg(unix)]
impl Unreadable {
  fn new(path: PathBuf) -> Self {
    let permissions = std::os::unix::fs::PermissionsExt::from_mode(0o300);
    fs::set_permissions(&path, permissions).unwrap();
    Self(path)
  }
}

#[cfg(unix)]
impl Drop for Unreadable {
  fn drop(&mut self) {
    let permissions = std::os::unix::fs::PermissionsExt::from_mode(0o755);
    let _ = fs::set_permissions(&self.0, permissions);
  }
}

#[cfg(unix)]
#[test]
fn probe_first_justfile_search_skips_unreadable_directory() {
  let test = Test::new()
    .test_round_trip(false)
    .justfile("default:\n\techo ok\n")
    .create_dir("sub")
    .current_dir("sub");

  let _unreadable = Unreadable::new(test.tempdir.path().join("sub"));

  test.stderr("echo ok\n").stdout("ok\n").success();
}

#[cfg(unix)]
#[test]
fn probe_second_init_skips_unreadable_directory() {
  let test = Test::new()
    .test_round_trip(false)
    .create_dir(".git")
    .create_dir("sub")
    .current_dir("sub");

  let _unreadable = Unreadable::new(test.tempdir.path().join("sub"));

  test
    .arg("--init")
    .stderr_regex("Wrote justfile to `.*`\n")
    .expect_file("justfile", just::INIT_JUSTFILE)
    .success();
}
