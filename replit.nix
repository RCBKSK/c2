{pkgs}: {
  deps = [
    pkgs.pkg-config
    pkgs.libffi
    pkgs.cacert
    pkgs.glibcLocales
    pkgs.iana-etc
  ];
}
