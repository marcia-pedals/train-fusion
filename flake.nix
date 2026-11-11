{
  description = "train-fusion: HMM fusion of Caltrain GTFS-RT feeds";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        # terraform is BSL-licensed, which nixpkgs counts as unfree (so it builds from source).
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfreePredicate = p: (nixpkgs.lib.getName p) == "terraform";
        };
        # shapely and pyproj aren't in the binary cache for this nixpkgs revision on macOS;
        # building them is quick, but their test suites pull in ~80 more packages, so skip tests.
        noCheck = p: p.overridePythonAttrs (_: { doCheck = false; });
        python = pkgs.python312.withPackages (ps: with ps; [
          gtfs-realtime-bindings
          protobuf
          numpy
          scipy
          polars
          pyarrow
          (noCheck shapely)
          (noCheck pyproj)
          pytest
          google-cloud-storage
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [ python pkgs.curl pkgs.jq pkgs.just pkgs.nodejs_22
                       pkgs.google-cloud-sdk pkgs.terraform pkgs.gh ];
        };
      });
}
