# -*- coding: utf-8 -*-
"""User input collection for Flat Slab Rebar placement — WPF dark UI."""
from __future__ import print_function

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from pyrevit import forms
from Autodesk.Revit.DB import FilteredElementCollector, BuiltInParameter, Group
from Autodesk.Revit.DB.Structure import RebarBarType, RebarHookType

from System.Windows.Markup import XamlReader
from System.Windows import Window, Visibility as WpfVisibility
from System.Windows.Controls import ComboBoxItem
from System.Windows.Input import MouseButton

MM_TO_FEET = 0.00328084
M_TO_FEET  = 3.28084


# ──────────────────────────────────────────────────────────────────────────────
#  XAML  —  dark-mode WPF form
# ──────────────────────────────────────────────────────────────────────────────
_XAML = u"""<Window
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="FlatSlabRFT"
    Width="720" Height="880"
    WindowStartupLocation="CenterScreen"
    ResizeMode="NoResize"
    WindowStyle="None"
    Background="#09090F">

  <Window.Resources>

    <!-- ── Input field ── -->
    <Style x:Key="DarkInput" TargetType="TextBox">
      <Setter Property="Background"        Value="#1E293B"/>
      <Setter Property="Foreground"        Value="#F1F5F9"/>
      <Setter Property="BorderBrush"       Value="#334155"/>
      <Setter Property="BorderThickness"   Value="1"/>
      <Setter Property="Padding"           Value="10,8"/>
      <Setter Property="FontSize"          Value="13"/>
      <Setter Property="FontFamily"        Value="Segoe UI"/>
      <Setter Property="CaretBrush"        Value="#06B6D4"/>
      <Setter Property="SelectionBrush"    Value="#06B6D4"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="TextBox">
            <Border x:Name="bd"
                    Background="{TemplateBinding Background}"
                    BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}"
                    CornerRadius="7"
                    Padding="{TemplateBinding Padding}">
              <ScrollViewer x:Name="PART_ContentHost"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsFocused" Value="True">
                <Setter TargetName="bd" Property="BorderBrush" Value="#06B6D4"/>
              </Trigger>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="bd" Property="BorderBrush" Value="#475569"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- ── Field label ── -->
    <Style x:Key="FieldLabel" TargetType="TextBlock">
      <Setter Property="Foreground"  Value="#64748B"/>
      <Setter Property="FontSize"    Value="10"/>
      <Setter Property="FontWeight"  Value="SemiBold"/>
      <Setter Property="FontFamily"  Value="Segoe UI"/>
      <Setter Property="Margin"      Value="0,0,0,5"/>
    </Style>

    <!-- ── Section header ── -->
    <Style x:Key="SectionHeader" TargetType="TextBlock">
      <Setter Property="Foreground"  Value="#06B6D4"/>
      <Setter Property="FontSize"    Value="10"/>
      <Setter Property="FontWeight"  Value="Bold"/>
      <Setter Property="FontFamily"  Value="Segoe UI"/>
      <Setter Property="Margin"      Value="0,0,0,12"/>
    </Style>

    <!-- ── Toggle radio button ── -->
    <Style x:Key="ToggleBtn" TargetType="RadioButton">
      <Setter Property="Foreground"  Value="#94A3B8"/>
      <Setter Property="FontFamily"  Value="Segoe UI"/>
      <Setter Property="FontSize"    Value="12"/>
      <Setter Property="FontWeight"  Value="SemiBold"/>
      <Setter Property="Cursor"      Value="Hand"/>
      <Setter Property="Margin"      Value="0,0,8,0"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="RadioButton">
            <Border x:Name="Root"
                    Background="#1E293B"
                    BorderBrush="#334155"
                    BorderThickness="1"
                    CornerRadius="7"
                    Padding="14,9">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsChecked" Value="True">
                <Setter TargetName="Root" Property="Background"   Value="#0E3A4A"/>
                <Setter TargetName="Root" Property="BorderBrush"  Value="#06B6D4"/>
                <Setter Property="Foreground" Value="#06B6D4"/>
              </Trigger>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="BorderBrush" Value="#475569"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- ── Run button (gradient) ── -->
    <Style x:Key="RunBtn" TargetType="Button">
      <Setter Property="Foreground"       Value="White"/>
      <Setter Property="FontFamily"       Value="Segoe UI"/>
      <Setter Property="FontSize"         Value="14"/>
      <Setter Property="FontWeight"       Value="Bold"/>
      <Setter Property="Padding"          Value="44,13"/>
      <Setter Property="BorderThickness"  Value="0"/>
      <Setter Property="Cursor"           Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root" CornerRadius="9" Padding="{TemplateBinding Padding}">
              <Border.Background>
                <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                  <GradientStop Color="#06B6D4" Offset="0"/>
                  <GradientStop Color="#8B5CF6" Offset="1"/>
                </LinearGradientBrush>
              </Border.Background>
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="Opacity" Value="0.88"/>
              </Trigger>
              <Trigger Property="IsPressed" Value="True">
                <Setter TargetName="Root" Property="Opacity" Value="0.70"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- ── Cancel button ── -->
    <Style x:Key="CancelBtn" TargetType="Button">
      <Setter Property="Background"      Value="#1E293B"/>
      <Setter Property="Foreground"      Value="#94A3B8"/>
      <Setter Property="FontFamily"      Value="Segoe UI"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="Padding"         Value="26,13"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root"
                    Background="{TemplateBinding Background}"
                    BorderBrush="#334155" BorderThickness="1"
                    CornerRadius="9"
                    Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="BorderBrush" Value="#475569"/>
                <Setter Property="Foreground" Value="#CBD5E1"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- ── Close button ── -->
    <Style x:Key="CloseBtn" TargetType="Button">
      <Setter Property="Background"     Value="Transparent"/>
      <Setter Property="Foreground"     Value="#475569"/>
      <Setter Property="FontSize"       Value="14"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"         Value="Hand"/>
      <Setter Property="Width"          Value="30"/>
      <Setter Property="Height"         Value="30"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root" Background="{TemplateBinding Background}" CornerRadius="5">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="Background" Value="#7F1D1D"/>
                <Setter Property="Foreground" Value="#FCA5A5"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- ── Card container ── -->
    <Style x:Key="Card" TargetType="Border">
      <Setter Property="Background"      Value="#111827"/>
      <Setter Property="BorderBrush"     Value="#1E3A5F"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius"    Value="10"/>
      <Setter Property="Padding"         Value="18,16"/>
      <Setter Property="Margin"          Value="0,0,0,10"/>
    </Style>

    <!-- ── Unit badge (cyan) ── -->
    <Style x:Key="UnitCyan" TargetType="Border">
      <Setter Property="Background"      Value="#0E3A4A"/>
      <Setter Property="BorderBrush"     Value="#06B6D4"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius"    Value="0,7,7,0"/>
      <Setter Property="Padding"         Value="10,8"/>
      <Setter Property="Margin"          Value="-1,0,0,0"/>
    </Style>

    <!-- ── Unit badge (purple) ── -->
    <Style x:Key="UnitPurple" TargetType="Border">
      <Setter Property="Background"      Value="#2D1B69"/>
      <Setter Property="BorderBrush"     Value="#8B5CF6"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius"    Value="0,7,7,0"/>
      <Setter Property="Padding"         Value="10,8"/>
      <Setter Property="Margin"          Value="-1,0,0,0"/>
    </Style>

    <!-- ComboBox: dark closed state + readable dropdown items -->
    <Style x:Key="DarkComboItem" TargetType="ComboBoxItem">
      <Setter Property="Foreground" Value="#111111"/>
      <Setter Property="Background" Value="White"/>
      <Setter Property="Padding" Value="8,4"/>
      <Style.Triggers>
        <Trigger Property="IsHighlighted" Value="True">
          <Setter Property="Background" Value="#D7ECFF"/>
          <Setter Property="Foreground" Value="#111111"/>
        </Trigger>
        <Trigger Property="IsSelected" Value="True">
          <Setter Property="Background" Value="#2B88D8"/>
          <Setter Property="Foreground" Value="White"/>
        </Trigger>
      </Style.Triggers>
    </Style>

    <Style x:Key="DarkCombo" TargetType="ComboBox">
      <Setter Property="Background" Value="#1E293B"/>
      <Setter Property="Foreground" Value="#111111"/>
      <Setter Property="BorderBrush" Value="#334155"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="Padding" Value="10,8"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="FontFamily" Value="Segoe UI"/>
      <Setter Property="ItemContainerStyle" Value="{StaticResource DarkComboItem}"/>
    </Style>

  </Window.Resources>

  <!-- Outer glow border -->
  <Border BorderBrush="#1E3A5F" BorderThickness="1" CornerRadius="0">
    <Grid>
      <Grid.RowDefinitions>
        <RowDefinition Height="40"/>   <!-- custom title bar   -->
        <RowDefinition Height="76"/>   <!-- app header         -->
        <RowDefinition Height="*"/>    <!-- scrollable content -->
        <RowDefinition Height="Auto"/> <!-- footer             -->
      </Grid.RowDefinitions>

      <!-- ═══ TITLE BAR ═══ -->
      <Border Grid.Row="0" x:Name="TitleBar" Background="#0D1117">
        <Grid>
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="Auto"/>
          </Grid.ColumnDefinitions>

          <StackPanel Grid.Column="0" Orientation="Horizontal" VerticalAlignment="Center" Margin="14,0">
            <Ellipse Width="9" Height="9" Margin="0,0,7,0">
              <Ellipse.Fill>
                <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
                  <GradientStop Color="#06B6D4" Offset="0"/>
                  <GradientStop Color="#8B5CF6" Offset="1"/>
                </LinearGradientBrush>
              </Ellipse.Fill>
            </Ellipse>
            <TextBlock Text="FlatSlabRFT" Foreground="#475569" FontSize="11"
                       FontFamily="Segoe UI" VerticalAlignment="Center"/>
          </StackPanel>

          <Border Grid.Column="1" x:Name="DragArea" Background="Transparent"/>

          <Button Grid.Column="2" x:Name="btnClose" Content="&#x2715;"
                  Style="{StaticResource CloseBtn}" Margin="0,0,8,0"/>
        </Grid>
      </Border>

      <!-- ═══ APP HEADER ═══ -->
      <Border Grid.Row="1" Padding="24,0">
        <Border.Background>
          <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
            <GradientStop Color="#0D1B2E" Offset="0"/>
            <GradientStop Color="#0A0E1A" Offset="1"/>
          </LinearGradientBrush>
        </Border.Background>
        <Grid VerticalAlignment="Center">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="Auto"/>
          </Grid.ColumnDefinitions>

          <StackPanel Grid.Column="0">
            <StackPanel Orientation="Horizontal">
              <Border Width="4" Height="34" CornerRadius="2" Margin="0,0,14,0">
                <Border.Background>
                  <LinearGradientBrush StartPoint="0,0" EndPoint="0,1">
                    <GradientStop Color="#06B6D4" Offset="0"/>
                    <GradientStop Color="#8B5CF6" Offset="1"/>
                  </LinearGradientBrush>
                </Border.Background>
              </Border>
              <TextBlock Text="FLAT SLAB RFT" FontSize="24" FontWeight="Bold"
                         Foreground="#F1F5F9" FontFamily="Segoe UI" VerticalAlignment="Center"/>
            </StackPanel>
            <TextBlock Text="Automated Reinforcement Placement Engine"
                       FontSize="12" Foreground="#475569" FontFamily="Segoe UI" Margin="18,4,0,0"/>
          </StackPanel>

          <Border Grid.Column="1" CornerRadius="20" Padding="14,6" VerticalAlignment="Center"
                  BorderThickness="1">
            <Border.Background>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#0E3A4A" Offset="0"/>
                <GradientStop Color="#2D1B69" Offset="1"/>
              </LinearGradientBrush>
            </Border.Background>
            <Border.BorderBrush>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#06B6D4" Offset="0"/>
                <GradientStop Color="#8B5CF6" Offset="1"/>
              </LinearGradientBrush>
            </Border.BorderBrush>
            <TextBlock FontFamily="Segoe UI" FontSize="11" FontWeight="Bold">
              <Run Text="BIM" Foreground="#06B6D4"/>
              <Run Text=" · " Foreground="#475569"/>
              <Run Text="RFT" Foreground="#8B5CF6"/>
            </TextBlock>
          </Border>
        </Grid>
      </Border>

      <!-- gradient divider -->
      <Border Grid.Row="1" VerticalAlignment="Bottom" Height="1">
        <Border.Background>
          <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
            <GradientStop Color="#06B6D4" Offset="0"/>
            <GradientStop Color="#8B5CF6" Offset="0.5"/>
            <GradientStop Color="Transparent" Offset="1"/>
          </LinearGradientBrush>
        </Border.Background>
      </Border>

      <!-- ═══ SCROLLABLE CONTENT ═══ -->
      <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto" Background="#09090F">
        <StackPanel Margin="24,18,24,8">

          <!-- ── PLACEMENT TYPE ── -->
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="PLACEMENT TYPE" Style="{StaticResource SectionHeader}"/>
              <StackPanel Orientation="Horizontal">
                <RadioButton x:Name="rbAddRFT"  Content="Add RFT"  GroupName="PT"
                             Style="{StaticResource ToggleBtn}"/>
                <RadioButton x:Name="rbMeshRFT" Content="Mesh RFT" GroupName="PT"
                             Style="{StaticResource ToggleBtn}" IsChecked="True"/>
                <RadioButton x:Name="rbBoth"    Content="Both"     GroupName="PT"
                             Style="{StaticResource ToggleBtn}"/>
              </StackPanel>
            </StackPanel>
          </Border>

          <!-- ── BAR PARAMS  +  STRUCTURAL PARAMS (side-by-side) ── -->
          <Grid x:Name="gridMainParams" Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="*"/>
              <ColumnDefinition Width="12"/>
              <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>

            <!-- LEFT — Bar Parameters -->
            <Border Grid.Column="0" Style="{StaticResource Card}" Margin="0">
              <StackPanel>
                <TextBlock Text="BAR PARAMETERS" Style="{StaticResource SectionHeader}"/>

                <TextBlock x:Name="lblDiam" Text="DIAMETER" Style="{StaticResource FieldLabel}"/>
                <Grid x:Name="rowDiam" Margin="0,0,0,12">
                  <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                  </Grid.ColumnDefinitions>
                  <TextBox x:Name="tbDiam" Text="12" Style="{StaticResource DarkInput}"/>
                  <Border Grid.Column="1" Style="{StaticResource UnitCyan}">
                    <TextBlock Text="mm" Foreground="#06B6D4" FontSize="12"
                               FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                  </Border>
                </Grid>

                <TextBlock x:Name="lblSpacing" Text="SPACING" Style="{StaticResource FieldLabel}"/>
                <Grid x:Name="rowSpacing" Margin="0,0,0,12">
                  <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                  </Grid.ColumnDefinitions>
                  <TextBox x:Name="tbSpacing" Text="200" Style="{StaticResource DarkInput}"/>
                  <Border Grid.Column="1" Style="{StaticResource UnitCyan}">
                    <TextBlock Text="mm" Foreground="#06B6D4" FontSize="12"
                               FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                  </Border>
                </Grid>

                <TextBlock Text="BAR LENGTH" Style="{StaticResource FieldLabel}"/>
                <Grid Margin="0,0,0,12">
                  <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                  </Grid.ColumnDefinitions>
                  <TextBox x:Name="tbBarLen" Text="12" Style="{StaticResource DarkInput}"/>
                  <Border Grid.Column="1" Style="{StaticResource UnitCyan}">
                    <TextBlock Text="m" Foreground="#06B6D4" FontSize="12"
                               FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                  </Border>
                </Grid>

                <TextBlock Text="STOCK LENGTHS" Style="{StaticResource FieldLabel}"/>
                <TextBox x:Name="tbStock" Text="12, 9, 6" Style="{StaticResource DarkInput}"
                         ToolTip="Comma-separated available bar lengths in metres, e.g.  12, 9, 6"/>
              </StackPanel>
            </Border>

            <!-- RIGHT — Structural Parameters -->
            <Border Grid.Column="2" Style="{StaticResource Card}" Margin="0">
              <StackPanel>
                <TextBlock Text="STRUCTURAL PARAMETERS" Style="{StaticResource SectionHeader}"/>

                <TextBlock Text="SPLICE MULTIPLIER" Style="{StaticResource FieldLabel}"/>
                <Grid Margin="0,0,0,12">
                  <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                  </Grid.ColumnDefinitions>
                  <TextBox x:Name="tbSplice" Text="50" Style="{StaticResource DarkInput}"/>
                  <Border Grid.Column="1" Style="{StaticResource UnitPurple}">
                    <TextBlock Text="&#xD7; &#x2300;" Foreground="#8B5CF6" FontSize="12"
                               FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                  </Border>
                </Grid>

                <TextBlock Text="LD MULTIPLIER" Style="{StaticResource FieldLabel}"/>
                <Grid Margin="0,0,0,12">
                  <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                  </Grid.ColumnDefinitions>
                  <TextBox x:Name="tbLd" Text="40" Style="{StaticResource DarkInput}"/>
                  <Border Grid.Column="1" Style="{StaticResource UnitPurple}">
                    <TextBlock Text="&#xD7; &#x2300;" Foreground="#8B5CF6" FontSize="12"
                               FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                  </Border>
                </Grid>

                <TextBlock Text="DP HORIZONTAL LEG" Style="{StaticResource FieldLabel}"/>
                <Grid Margin="0,0,0,12">
                  <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                  </Grid.ColumnDefinitions>
                  <TextBox x:Name="tbDpHoriz" Text="300" Style="{StaticResource DarkInput}"/>
                  <Border Grid.Column="1" Style="{StaticResource UnitCyan}">
                    <TextBlock Text="mm" Foreground="#06B6D4" FontSize="12"
                               FontWeight="SemiBold" FontFamily="Segoe UI" VerticalAlignment="Center"/>
                  </Border>
                </Grid>

                <TextBlock Text="HOOK TYPE" Style="{StaticResource FieldLabel}"/>
                <ComboBox x:Name="cbHook" Margin="0,0,0,12"
                          Style="{StaticResource DarkCombo}"/>

                <TextBlock x:Name="lblBarType" Text="BAR TYPE" Style="{StaticResource FieldLabel}"/>
                <ComboBox x:Name="cbBar"
                          Style="{StaticResource DarkCombo}"/>
              </StackPanel>
            </Border>
          </Grid>

          <Border x:Name="cardAddRft" Style="{StaticResource Card}" Visibility="Collapsed">
            <StackPanel>
              <TextBlock Text="ADD RFT DETAIL GROUPS" Style="{StaticResource SectionHeader}"/>
              <TextBlock Text="Select detail groups from the active view for each layer and direction."
                         Foreground="#94A3B8" FontSize="11" FontFamily="Segoe UI" Margin="0,0,0,10"/>
              <TextBlock Text="BOTTOM X" Style="{StaticResource FieldLabel}"/>
              <ComboBox x:Name="cbAddBotX" Margin="0,0,0,10" Style="{StaticResource DarkCombo}"/>
              <TextBlock Text="BOTTOM Y" Style="{StaticResource FieldLabel}"/>
              <ComboBox x:Name="cbAddBotY" Margin="0,0,0,10" Style="{StaticResource DarkCombo}"/>
              <TextBlock Text="TOP X" Style="{StaticResource FieldLabel}"/>
              <ComboBox x:Name="cbAddTopX" Margin="0,0,0,10" Style="{StaticResource DarkCombo}"/>
              <TextBlock Text="TOP Y" Style="{StaticResource FieldLabel}"/>
              <ComboBox x:Name="cbAddTopY" Style="{StaticResource DarkCombo}"/>
            </StackPanel>
          </Border>

          <!-- ── RUN MODE ── -->
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="RUN MODE" Style="{StaticResource SectionHeader}"/>
              <WrapPanel>
                <RadioButton x:Name="rbDirect"   Content="Place Directly"    GroupName="RM"
                             Style="{StaticResource ToggleBtn}" Margin="0,0,8,8" IsChecked="True"/>
                <RadioButton x:Name="rbPrevCfm"  Content="Preview + Confirm" GroupName="RM"
                             Style="{StaticResource ToggleBtn}" Margin="0,0,8,8"/>
                <RadioButton x:Name="rbPrevOnly" Content="Preview Only"      GroupName="RM"
                             Style="{StaticResource ToggleBtn}" Margin="0,0,8,8"/>
                <RadioButton x:Name="rbDPOnly"   Content="Place DP Only"     GroupName="RM"
                             Style="{StaticResource ToggleBtn}" Margin="0,0,8,8"/>
              </WrapPanel>

              <StackPanel x:Name="pnlPreview" Orientation="Horizontal"
                          Margin="0,8,0,0" Visibility="Collapsed">
                <TextBlock Text="MAX PREVIEW LINES" Style="{StaticResource FieldLabel}"
                           VerticalAlignment="Center" Margin="0,0,12,4"/>
                <TextBox x:Name="tbPrevLines" Text="1200" Width="130"
                         Style="{StaticResource DarkInput}"/>
              </StackPanel>
            </StackPanel>
          </Border>

        </StackPanel>
      </ScrollViewer>

      <!-- ═══ FOOTER ═══ -->
      <Border Grid.Row="3" Background="#0D1117" Padding="24,16,24,22">
        <StackPanel>

          <!-- divider -->
          <Border Height="1" Margin="0,0,0,16">
            <Border.Background>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="Transparent" Offset="0"/>
                <GradientStop Color="#334155"     Offset="0.5"/>
                <GradientStop Color="Transparent" Offset="1"/>
              </LinearGradientBrush>
            </Border.Background>
          </Border>

          <!-- action buttons -->
          <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btnCancel" Content="Cancel"
                    Style="{StaticResource CancelBtn}" Margin="0,0,12,0"/>
            <Button x:Name="btnRun" Content="&#x25B6;  Run Placement"
                    Style="{StaticResource RunBtn}"/>
          </StackPanel>

          <!-- ── SIGNATURE ── -->
          <Border Margin="0,18,0,0" HorizontalAlignment="Center">
            <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
              <Border Width="6" Height="6" CornerRadius="3" Margin="0,0,10,0"
                      VerticalAlignment="Center">
                <Border.Background>
                  <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                    <GradientStop Color="#06B6D4" Offset="0"/>
                    <GradientStop Color="#8B5CF6" Offset="1"/>
                  </LinearGradientBrush>
                </Border.Background>
              </Border>
              <TextBlock FontFamily="Segoe UI" FontSize="12" VerticalAlignment="Center">
                <Run Text="Developed by " Foreground="#475569"/>
                <Run Text="Nour Waleed" Foreground="#06B6D4" FontWeight="Bold"/>
                <Run Text="  &#xB7;  FlatSlabRFT Engine" Foreground="#334155"/>
              </TextBlock>
            </StackPanel>
          </Border>

        </StackPanel>
      </Border>

    </Grid>
  </Border>
</Window>"""


# ──────────────────────────────────────────────────────────────────────────────
#  Helper utilities (shared with legacy code paths)
# ──────────────────────────────────────────────────────────────────────────────

def _build_detail_group_map(doc, view=None):
    """Return {display_name: Group} for all detail groups in *view* (or whole doc)."""
    if view is not None:
        all_groups = list(FilteredElementCollector(doc, view.Id).OfClass(Group).ToElements())
    else:
        all_groups = list(FilteredElementCollector(doc).OfClass(Group).ToElements())

    name_to_group = {}
    for g in all_groups:
        try:
            cat = g.Category
            if cat is None or 'detail' not in cat.Name.lower():
                continue
            gtype = doc.GetElement(g.GetTypeId())
            name = _type_name(gtype) if gtype is not None else str(g.Id.IntegerValue)
        except Exception:
            continue
        base = name
        k = 1
        while name in name_to_group:
            name = '{} ({})'.format(base, k)
            k += 1
        name_to_group[name] = g
    return name_to_group


def pick_add_rft_group(doc, prompt_title, view=None):
    """Show a list of detail groups and a direction prompt for the user to choose from."""
    name_to_group = _build_detail_group_map(doc, view)
    if not name_to_group:
        return None

    SKIP = '-- Skip (no add-rft group) --'
    choice = forms.SelectFromList.show(
        [SKIP] + sorted(name_to_group.keys()),
        title=prompt_title,
        multiselect=False
    )
    if choice is None or choice == SKIP:
        return None

    group = name_to_group.get(choice)
    if group is None:
        return None

    direction = forms.SelectFromList.show(
        ['X', 'Y'],
        title='Bar direction for "{}"'.format(choice),
        multiselect=False
    )
    if direction is None:
        return None

    return {'group': group, 'direction': direction}


def _type_name(element):
    """Return the display name of a Revit ElementType via its built-in parameter."""
    param = element.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
    if param is not None:
        val = param.AsString()
        if val:
            return val
    return str(element.Id.IntegerValue)


def _parse_stock_lengths(raw, default=None):
    """Parse a comma-separated string of stock bar lengths (m) → sorted list (largest first)."""
    if default is None:
        default = [12.0, 9.0, 6.0]
    if not raw:
        return default
    lengths = []
    for tok in raw.replace(';', ',').split(','):
        tok = tok.strip()
        if not tok:
            continue
        try:
            v = float(tok)
            if 0.5 <= v <= 20.0:
                lengths.append(v)
        except (ValueError, TypeError):
            pass
    if not lengths:
        forms.alert(
            'Could not parse any valid stock lengths from "{}". '
            'Using defaults: {}'.format(raw, default),
            title='Stock Lengths'
        )
        return default
    return sorted(set(lengths), reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
#  WPF dialog
# ──────────────────────────────────────────────────────────────────────────────

def _show_main_dialog(doc, view=None):
    """Show the dark WPF form and return a params dict, or None if cancelled."""
    hook_types   = list(FilteredElementCollector(doc).OfClass(RebarHookType).ToElements())
    bar_types_all = list(FilteredElementCollector(doc).OfClass(RebarBarType).ToElements())

    if not hook_types:
        raise Exception('No RebarHookType elements found in document.')

    hook_type_dict = {_type_name(ht): ht for ht in hook_types}
    bar_type_dict  = {_type_name(bt): bt for bt in bar_types_all}

    window = XamlReader.Parse(_XAML)

    # ── find controls ──────────────────────────────────────────────────────
    rb_add_rft    = window.FindName('rbAddRFT')
    rb_mesh_rft   = window.FindName('rbMeshRFT')
    rb_both       = window.FindName('rbBoth')
    grid_main_params = window.FindName('gridMainParams')
    lbl_diam      = window.FindName('lblDiam')
    row_diam      = window.FindName('rowDiam')
    tb_diam       = window.FindName('tbDiam')
    lbl_spacing   = window.FindName('lblSpacing')
    row_spacing   = window.FindName('rowSpacing')
    tb_spacing    = window.FindName('tbSpacing')
    tb_bar_len    = window.FindName('tbBarLen')
    tb_stock      = window.FindName('tbStock')
    tb_splice     = window.FindName('tbSplice')
    tb_ld         = window.FindName('tbLd')
    tb_dp_horiz   = window.FindName('tbDpHoriz')
    cb_hook       = window.FindName('cbHook')
    lbl_bar_type  = window.FindName('lblBarType')
    cb_bar        = window.FindName('cbBar')
    rb_direct     = window.FindName('rbDirect')
    rb_prev_cfm   = window.FindName('rbPrevCfm')
    rb_prev_only  = window.FindName('rbPrevOnly')
    rb_dp_only    = window.FindName('rbDPOnly')
    pnl_preview   = window.FindName('pnlPreview')
    tb_prev_lines = window.FindName('tbPrevLines')
    card_add_rft  = window.FindName('cardAddRft')
    cb_add_bot_x  = window.FindName('cbAddBotX')
    cb_add_bot_y  = window.FindName('cbAddBotY')
    cb_add_top_x  = window.FindName('cbAddTopX')
    cb_add_top_y  = window.FindName('cbAddTopY')
    btn_run       = window.FindName('btnRun')
    btn_cancel    = window.FindName('btnCancel')
    btn_close     = window.FindName('btnClose')
    title_bar     = window.FindName('TitleBar')

    # ── populate hook type combo ───────────────────────────────────────────
    for name in sorted(hook_type_dict.keys()):
        item = ComboBoxItem()
        item.Content = name
        item.Tag = hook_type_dict[name]
        cb_hook.Items.Add(item)
    if cb_hook.Items.Count > 0:
        cb_hook.SelectedIndex = 0

    # ── populate bar type combo (filtered by diameter) ────────────────────
    def _rebuild_bar_combo(diam_mm=None):
        cb_bar.Items.Clear()
        matching = {}
        if diam_mm is not None and diam_mm > 0:
            for bt in bar_types_all:
                try:
                    p = bt.LookupParameter('Bar Diameter') or bt.LookupParameter('Nominal Diameter')
                    if p is not None:
                        bt_d = p.AsDouble() / MM_TO_FEET
                        if abs(bt_d - diam_mm) < 1.0:
                            matching[_type_name(bt)] = bt
                except Exception:
                    pass
        if not matching:
            matching = dict(bar_type_dict)
        for name in sorted(matching.keys()):
            item = ComboBoxItem()
            item.Content = name
            item.Tag = matching[name]
            cb_bar.Items.Add(item)
        if cb_bar.Items.Count > 0:
            cb_bar.SelectedIndex = 0

    _rebuild_bar_combo(12.0)
    name_to_group = _build_detail_group_map(doc, view)

    def _populate_group_combo(cb):
        cb.Items.Clear()
        skip = ComboBoxItem()
        skip.Content = '-- Skip --'
        skip.Tag = None
        cb.Items.Add(skip)
        for name in sorted(name_to_group.keys()):
            item = ComboBoxItem()
            item.Content = name
            item.Tag = name_to_group[name]
            cb.Items.Add(item)
        cb.SelectedIndex = 0

    _populate_group_combo(cb_add_bot_x)
    _populate_group_combo(cb_add_bot_y)
    _populate_group_combo(cb_add_top_x)
    _populate_group_combo(cb_add_top_y)

    # ── result holder ──────────────────────────────────────────────────────
    result = [None]

    # ── visibility helpers ─────────────────────────────────────────────────
    def _update_placement_visibility(sender=None, e=None):
        is_add = bool(rb_add_rft.IsChecked)
        mesh_vis = WpfVisibility.Collapsed if is_add else WpfVisibility.Visible
        add_vis = WpfVisibility.Visible if (is_add or bool(rb_both.IsChecked)) else WpfVisibility.Collapsed
        dp_only_vis = WpfVisibility.Collapsed if is_add else WpfVisibility.Visible
        if is_add and bool(rb_dp_only.IsChecked):
            rb_direct.IsChecked = True
        lbl_diam.Visibility    = mesh_vis
        row_diam.Visibility    = mesh_vis
        lbl_spacing.Visibility  = mesh_vis
        row_spacing.Visibility  = mesh_vis
        lbl_bar_type.Visibility = mesh_vis
        cb_bar.Visibility       = mesh_vis
        grid_main_params.Visibility = mesh_vis
        rb_dp_only.Visibility   = dp_only_vis
        card_add_rft.Visibility = add_vis

    def _update_preview_visibility(sender=None, e=None):
        show = bool(rb_prev_cfm.IsChecked) or bool(rb_prev_only.IsChecked)
        pnl_preview.Visibility = WpfVisibility.Visible if show else WpfVisibility.Collapsed

    def _on_diam_changed(sender, e):
        try:
            _rebuild_bar_combo(float(str(tb_diam.Text).strip()))
        except (ValueError, TypeError):
            _rebuild_bar_combo(None)

    # ── run handler ────────────────────────────────────────────────────────
    def _on_run(sender, e):
        is_add_rft = bool(rb_add_rft.IsChecked)

        # Add RFT inherits geometry/details from selected detail groups.
        # Keep legacy defaults internally for downstream calculations.
        diam_mm = 12.0
        spacing_mm = 200.0
        bar_length_m = 12.0
        splice_mult = 50.0
        ld_mult = 40.0
        dp_horiz_mm = 300.0
        stock_lengths = [12.0, 9.0, 6.0]

        if not is_add_rft:
            try:
                diam_mm = float(str(tb_diam.Text).strip())
                assert diam_mm > 0
            except Exception:
                forms.alert('Invalid bar diameter.', title='Input Error')
                return
            try:
                spacing_mm = float(str(tb_spacing.Text).strip())
                assert spacing_mm > 0
            except Exception:
                forms.alert('Invalid bar spacing.', title='Input Error')
                return
            try:
                bar_length_m = float(str(tb_bar_len.Text).strip())
                assert bar_length_m > 0
            except Exception:
                forms.alert('Invalid bar length.', title='Input Error')
                return
            try:
                splice_mult = float(str(tb_splice.Text).strip())
                assert splice_mult > 0
            except Exception:
                forms.alert('Invalid splice multiplier.', title='Input Error')
                return
            try:
                ld_mult = float(str(tb_ld.Text).strip())
                assert ld_mult > 0
            except Exception:
                forms.alert('Invalid Ld multiplier.', title='Input Error')
                return
            try:
                dp_horiz_mm = float(str(tb_dp_horiz.Text).strip())
                assert dp_horiz_mm > 0
            except Exception:
                forms.alert('Invalid DP horizontal leg.', title='Input Error')
                return
            stock_lengths = _parse_stock_lengths(str(tb_stock.Text).strip())

        hook_item = cb_hook.SelectedItem
        hook_type = hook_item.Tag if hook_item is not None else None
        if hook_type is None and cb_hook.Items.Count > 0:
            hook_type = cb_hook.Items[0].Tag
        if hook_type is None:
            forms.alert('No hook type available in this project.', title='Input Error')
            return

        bar_type = None
        if not is_add_rft:
            bar_item = cb_bar.SelectedItem
            if bar_item is None:
                forms.alert('Please select a bar type.', title='Input Error')
                return
            bar_type = bar_item.Tag

        if bool(rb_prev_cfm.IsChecked):
            run_mode = 'Preview + Confirm'
        elif bool(rb_prev_only.IsChecked):
            run_mode = 'Preview Only'
        elif bool(rb_dp_only.IsChecked):
            run_mode = 'Place DP Only'
        else:
            run_mode = 'Place Directly'

        preview_max_lines = 1200
        if run_mode in ('Preview + Confirm', 'Preview Only'):
            try:
                preview_max_lines = int(float(str(tb_prev_lines.Text).strip()))
                assert preview_max_lines > 0
            except Exception:
                forms.alert('Invalid preview lines count.', title='Input Error')
                return

        if bool(rb_add_rft.IsChecked):
            placement_type = 'Add RFT'
        elif bool(rb_both.IsChecked):
            placement_type = 'Both'
        else:
            placement_type = 'Mesh RFT'

        add_rft_entries = []
        for _cb, _layer, _dir in [
            (cb_add_bot_x, 'bottom', 'X'),
            (cb_add_bot_y, 'bottom', 'Y'),
            (cb_add_top_x, 'top', 'X'),
            (cb_add_top_y, 'top', 'Y'),
        ]:
            _item = _cb.SelectedItem
            _group = _item.Tag if _item is not None else None
            if _group is None:
                continue
            add_rft_entries.append({
                'group': _group,
                'layer': _layer,
                'direction': _dir,
            })

        if placement_type == 'Add RFT' and not add_rft_entries:
            forms.alert('Please select at least one Add RFT detail group.', title='Input Error')
            return

        diameter_ft      = diam_mm      * MM_TO_FEET
        spacing_ft       = spacing_mm   * MM_TO_FEET
        bar_length_ft    = bar_length_m * M_TO_FEET
        splice_length_ft = splice_mult  * diameter_ft
        ld_ft            = ld_mult      * diameter_ft
        dp_horiz_ft      = dp_horiz_mm  * MM_TO_FEET

        if (not is_add_rft) and (bar_length_ft <= spacing_ft):
            forms.alert('Bar length must be greater than spacing.', title='Input Error')
            return

        result[0] = {
            'placement_type':         placement_type,
            'diameter':               diameter_ft,
            'spacing':                spacing_ft,
            'bar_length':             bar_length_ft,
            'splice_multiplier':      splice_mult,
            'splice_length':          splice_length_ft,
            'ld_multiplier':          ld_mult,
            'ld':                     ld_ft,
            'dp_horizontal_leg':      dp_horiz_ft,
            'hook_type':              hook_type,
            'bar_type':               bar_type,
            'run_mode':               run_mode,
            'preview_max_lines':      preview_max_lines,
            'standard_bar_lengths_m': stock_lengths,
            'add_rft_entries':        add_rft_entries,
            'add_rft_bottom_group':   None,
            'add_rft_top_group':      None,
        }
        for _entry in add_rft_entries:
            if _entry['layer'] == 'bottom' and result[0]['add_rft_bottom_group'] is None:
                result[0]['add_rft_bottom_group'] = _entry['group']
            if _entry['layer'] == 'top' and result[0]['add_rft_top_group'] is None:
                result[0]['add_rft_top_group'] = _entry['group']
        window.Close()

    def _on_cancel(sender, e):
        window.Close()

    def _on_title_bar_down(sender, e):
        if e.ChangedButton == MouseButton.Left:
            window.DragMove()

    # ── wire events ────────────────────────────────────────────────────────
    rb_add_rft.Checked   += _update_placement_visibility
    rb_mesh_rft.Checked  += _update_placement_visibility
    rb_both.Checked      += _update_placement_visibility

    rb_direct.Checked    += _update_preview_visibility
    rb_prev_cfm.Checked  += _update_preview_visibility
    rb_prev_only.Checked += _update_preview_visibility
    rb_dp_only.Checked   += _update_preview_visibility

    tb_diam.TextChanged  += _on_diam_changed

    btn_run.Click    += _on_run
    btn_cancel.Click += _on_cancel
    btn_close.Click  += _on_cancel
    title_bar.MouseDown += _on_title_bar_down

    _update_placement_visibility()
    _update_preview_visibility()

    window.ShowDialog()
    return result[0]


# ──────────────────────────────────────────────────────────────────────────────
#  Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def collect_inputs(doc, view=None):
    """Show the main WPF dialog and return params.

    Returns a dict with all values in Revit internal units (feet).
    Raises SystemExit if the user cancels.
    """
    params = _show_main_dialog(doc, view)
    if params is None:
        raise SystemExit('User cancelled.')

    placement_type = params['placement_type']
    if placement_type == 'Add RFT' and not (params.get('add_rft_entries') or []):
        raise SystemExit('No Add RFT detail groups selected.')

    return params
