{
  description = "train-fusion: HMM fusion of Caltrain GTFS-RT feeds";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
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
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [ python pkgs.curl pkgs.jq pkgs.just ];
        };
      });
}
